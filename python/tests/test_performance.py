"""Moteur de performance : levier, plafonds pilotables, objectif du jour,
fermeture globale des positions.

Ces réglages remplacent des constantes autrefois figées dans le code. Les
tests vérifient donc DEUX choses : que chaque réglage est bien appliqué, et
qu'aucune valeur saisie ne peut sortir de ses bornes (un levier de 50 demandé
par un agent doit être rabattu, jamais appliqué).
"""
import _setup  # noqa: F401
import os
import tempfile
from pathlib import Path


def _isoler_config():
    """Config sur un chemin temporaire : la vraie config n'est jamais touchée."""
    from utils import app_config
    fd, chemin = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(chemin)
    app_config._PATH = Path(chemin)
    app_config._cache = None


def test_bornes_et_tout_ou_rien():
    _isoler_config()
    from utils import trading_config as tc
    tc.forcer_en_memoire()

    # Le levier max est borné à 10 : une saisie de 50 est rabattue, pas refusée.
    r = tc.configurer({"levier_max": 50, "levier": 50})
    assert r["success"], r
    assert tc.get("levier_max") == 10.0, tc.get("levier_max")
    assert tc.levier_actuel() == 10.0

    # Un plafond abaissé rabat IMMÉDIATEMENT le levier courant : sinon
    # l'interface afficherait « max 3 » pendant que le robot trade à 10.
    tc.configurer({"levier_max": 3})
    assert tc.get("levier") == 3.0 and tc.levier_actuel() == 3.0

    # Tout ou rien : une valeur invalide n'applique AUCUN des champs fournis.
    avant = tc.get("risque_par_trade_pct")
    r = tc.configurer({"risque_par_trade_pct": 12, "cooldown_s": "abc"})
    assert not r["success"] and tc.get("risque_par_trade_pct") == avant, r

    # Virgule décimale acceptée (même règle que partout ailleurs dans l'app)
    assert tc.configurer({"risque_par_trade_pct": "7,5"})["success"]
    assert tc.get("risque_par_trade_pct") == 7.5

    # Champ inconnu ignoré, sans casser les champs valides
    assert tc.configurer({"inconnu_xyz": 1, "cooldown_s": 0})["success"]
    assert tc.get("cooldown_s") == 0
    print("  OK — réglages : bornes appliquées, tout-ou-rien, virgule décimale")


def test_levier_agents():
    _isoler_config()
    from utils import trading_config as tc
    tc.forcer_en_memoire(levier_max=10.0, levier=5.0, levier_auto=True)

    # Un agent ne peut jamais dépasser le plafond de l'utilisateur.
    assert tc.ajuster_levier(50) == 10.0
    assert tc.ajuster_levier(0.1) == 1.0
    assert tc.ajuster_levier(4) == 4.0
    assert tc.tout()["levier_source"] == "agents IA"

    # Levier proposé : proportionnel à la conviction, réduit par la volatilité.
    assert tc.levier_suggere(100) == 10.0
    assert tc.levier_suggere(0) == 1.0
    calme = tc.levier_suggere(80)
    agite = tc.levier_suggere(80, volatilite_pct=8.0)
    assert agite < calme, (calme, agite)

    # Pilotage automatique coupé : la décision des agents n'a plus d'effet.
    tc.forcer_en_memoire(levier_max=10.0, levier=6.0, levier_auto=False)
    assert tc.ajuster_levier(10) == 6.0
    print("  OK — levier : borné par l'utilisateur, modulé par la conviction")


def test_volume_au_levier():
    _isoler_config()
    from utils import trading_config as tc
    tc.forcer_en_memoire(levier_max=10.0, levier=10.0, volume_max_lot=50.0)

    # 100 € d'équité, levier 10, BTC à 50 000, contrat de 1 → 1000/50000 = 0.02 lot
    assert tc.volume_cible(100, 50_000, 1.0, confiance=100) == 0.02
    # Confiance moitié → engagement moitié
    assert tc.volume_cible(100, 50_000, 1.0, confiance=50) == 0.01
    # Plafond « volume max » respecté (0.02 souhaité, plafond 0.01)
    tc.forcer_en_memoire(levier=10.0, volume_max_lot=0.01)
    assert tc.volume_cible(100, 50_000, 1.0, confiance=100) == 0.01
    # Données inexploitables → None (l'appelant garde son volume)
    assert tc.volume_cible(0, 50_000, 1.0) is None
    assert tc.volume_cible(100, 0, 1.0) is None
    assert tc.volume_cible(100, 50_000, 0) is None
    print("  OK — volume au levier : notionnel = levier × équité, plafonné")


def test_fermeture_globale():
    """Le bouton d'urgence de l'accueil ferme TOUTES les positions et rapporte
    les échecs — une position récalcitrante ne doit pas laisser les autres
    ouvertes."""
    _isoler_config()
    from utils.mt5_manager import MT5Manager

    m = MT5Manager()
    m._lib_available = False
    m._connected = True
    m._account_info = {"balance": 100, "equity": 100, "currency": "EUR"}

    # Aucune position : succès, rien à faire (pas d'erreur trompeuse)
    r = m.fermer_toutes_positions()
    assert r["success"] and r["fermees"] == 0, r

    # Deux positions simulées (le journal fait foi hors MetaTrader)
    for i, (sym, sens) in enumerate([("BTCUSD", "BUY"), ("EURUSD", "SELL")], start=1):
        m._journal.insert(0, {"timestamp": "10:0%d:00" % i, "symbol": sym,
                              "action": sens, "volume": 0.01, "price": 100.0,
                              "ticket": i, "success": True, "error": None,
                              "simulated": True})
    assert len(m.get_positions()) == 2
    r = m.fermer_toutes_positions()
    assert r["success"] and r["fermees"] == 2 and r["echecs"] == 0, r
    assert m.get_positions() == [], m.get_positions()

    # Un échec sur une position n'empêche pas de fermer les autres
    m2 = MT5Manager()
    m2._lib_available = False
    m2._connected = True

    class _Faux(MT5Manager):
        pass

    positions = [{"ticket": 1, "symbol": "BTCUSD"}, {"ticket": 2, "symbol": "EURUSD"}]
    m2.get_positions = lambda: positions
    m2.fermer_ticket = lambda t: ({"success": False, "error": "marché fermé"}
                                  if t == 1 else {"success": True})
    r = m2.fermer_toutes_positions()
    assert not r["success"] and r["fermees"] == 1 and r["echecs"] == 1, r
    assert any(d.get("erreur") == "marché fermé" for d in r["details"]), r
    print("  OK — fermeture globale : tout fermé, échecs rapportés sans tout stopper")


def test_objectif_du_jour():
    _isoler_config()
    from utils import trading_config as tc
    import utils.mt5_manager as _mm
    import utils.auto_trader as _at
    from utils.auto_trader import objectif_du_jour
    from datetime import datetime

    tc.forcer_en_memoire(objectif_journalier=80.0, capital_reference=100.0)
    # Le résultat est mis en cache 15 s : un appel fait par un test précédent
    # (via get_status) répondrait à la place du courtier de ce test.
    _at._objectif_cache["ts"] = 0.0
    _at._objectif_cache["valeur"] = None
    jour = datetime.now().strftime("%Y-%m-%d")

    class _Faux:
        def get_historique_reel(self, jours=1):
            return {"synced": True, "devise": "EUR",
                    "par_jour": [{"date": jour, "pnl": 30.0},
                                 {"date": "1999-01-01", "pnl": 999.0}]}
        def get_positions(self):
            return [{"profit": 12.5}, {"profit": -2.5}]

    _orig = _mm.get_mt5_manager
    _mm.get_mt5_manager = lambda: _Faux()
    try:
        o = objectif_du_jour()
    finally:
        _mm.get_mt5_manager = _orig

    assert o["realise"] == 30.0, o          # seul le jour EN COURS compte
    assert o["latent"] == 10.0, o           # 12.5 - 2.5
    assert o["total"] == 40.0 and o["progression_pct"] == 50.0, o
    assert not o["atteint"]
    # 80 € par jour sur 100 € de capital = 80 % par jour : affiché tel quel.
    assert o["rendement_vise_pct"] == 80.0, o
    print("  OK — objectif du jour : réalisé + latent, progression, rendement visé")


def test_cycle_transporte_levier_et_filtres():
    """Bout en bout : la décision des agents porte SON levier jusqu'à l'ordre,
    et les filtres réglables (confiance minimale, empilement) s'appliquent."""
    _isoler_config()
    import utils.mt5_manager as _mm
    import utils.risk_guard as _rg
    import utils.market_hours as _mh
    import backend.main as _bm
    from utils import trading_config as tc
    from utils.auto_trader import AutoTrader

    tc.forcer_en_memoire(levier_max=10.0, levier=8.0, confiance_min=50.0,
                         cooldown_s=0, anti_empilement=False,
                         max_positions_symbole=2)

    envoyes = []
    positions = []

    class _FakeMt5:
        _lib_available = True
        def est_reel(self): return False
        def get_status(self): return {"connected": True, "auto_trading": True}
        def get_portfolio(self): return {"equity": 100, "nb_positions": len(positions),
                                         "exposition_pct": 0}
        def directions_ouvertes(self, s): return []
        def resoudre_symbole(self, s): return s
        def get_positions(self): return list(positions)
        def get_journal(self, n=20): return []
        def set_auto_trading(self, v): pass
        def execute_trade(self, sig):
            envoyes.append(sig)
            positions.append({"symbol": sig["symbol"], "type": sig["action"],
                              "profit": 0.0})
            return {"success": True, "price": 100.0}

    class _RiskOK:
        def evaluer(self, _p): return (True, "")

    class _Chef:
        def orchestrer(self, syms):
            return {"decisions": {
                "AAA": {"action": "BUY", "confiance": 90, "prix_entree": 100.0,
                        "donnees": {"levier": 6.0}},
                # sous le seuil de confiance : ignoré
                "BBB": {"action": "BUY", "confiance": 20, "prix_entree": 100.0,
                        "donnees": {"levier": 6.0}},
            }}

    _o = (_mm.get_mt5_manager, _rg.get_risk_guard, _mh.marche_ouvert,
          _bm.get_orchestrateur)
    _mm.get_mt5_manager = lambda: _FakeMt5()
    _rg.get_risk_guard = lambda: _RiskOK()
    _mh.marche_ouvert = lambda s: (True, "")
    _bm.get_orchestrateur = lambda: _Chef()
    try:
        at = AutoTrader()
        at._running = True
        at._symboles = ["AAA", "BBB"]
        at._cycle_analyse(gen=at._generation)

        assert [s["symbol"] for s in envoyes] == ["AAA"], envoyes
        assert envoyes[0]["levier"] == 6.0, envoyes[0]     # celui de la décision
        assert envoyes[0]["confiance"] == 90, envoyes[0]   # sert au dimensionnement
        assert envoyes[0]["stop_loss"] > 0, envoyes[0]     # stop complété

        # Empilement : 2 positions autorisées sur le symbole, la 3e est refusée.
        at._cycle_analyse(gen=at._generation)
        assert len(envoyes) == 2, envoyes
        at._cycle_analyse(gen=at._generation)
        assert len(envoyes) == 2, "plafond de positions par symbole non appliqué"
    finally:
        (_mm.get_mt5_manager, _rg.get_risk_guard, _mh.marche_ouvert,
         _bm.get_orchestrateur) = _o
    print("  OK — cycle : levier de la décision transmis, filtres réglables appliqués")


def test_motifs_abstention():
    """« Aucun ordre » doit toujours s'expliquer.

    Chaque abandon du cycle (pas de consensus, marché fermé, cooldown, plafond
    par symbole, sécurité) ne laissait qu'une ligne dans les journaux du
    serveur : l'interface affichait « Aucun ordre exécuté » sans motif, et un
    robot qui appliquait ses règles était indiscernable d'un robot en panne.
    """
    _isoler_config()
    import utils.mt5_manager as _mm
    import utils.risk_guard as _rg
    import utils.market_hours as _mh
    import backend.main as _bm
    from utils import trading_config as tc
    from utils.auto_trader import AutoTrader

    tc.forcer_en_memoire(cooldown_s=3600, confiance_min=0, anti_empilement=False,
                         max_positions_symbole=3)

    class _FakeMt5:
        _lib_available = True
        def est_reel(self): return False
        def get_status(self): return {"connected": True, "auto_trading": True}
        def get_portfolio(self): return {"equity": 100, "nb_positions": 0,
                                         "exposition_pct": 0}
        def directions_ouvertes(self, s): return []
        def resoudre_symbole(self, s): return s
        def get_positions(self): return []
        def get_journal(self, n=20): return []
        def set_auto_trading(self, v): pass
        def execute_trade(self, sig): return {"success": True, "price": 100.0}

    class _RiskOK:
        def evaluer(self, _p): return (True, "")

    class _Chef:
        def orchestrer(self, syms):
            return {"decisions": {
                "AAA": {"action": "BUY", "confiance": 80, "prix_entree": 100.0},
                "HHH": {"action": "HOLD", "confiance": 50, "prix_entree": 100.0},
                "FFF": {"action": "BUY", "confiance": 80, "prix_entree": 100.0},
            }}

    _o = (_mm.get_mt5_manager, _rg.get_risk_guard, _mh.marche_ouvert,
          _bm.get_orchestrateur)
    _mm.get_mt5_manager = lambda: _FakeMt5()
    _rg.get_risk_guard = lambda: _RiskOK()
    # FFF : marché fermé ; les autres ouverts
    _mh.marche_ouvert = lambda s: (False, "marché fermé (week-end)") if s == "FFF" else (True, "")
    _bm.get_orchestrateur = lambda: _Chef()
    try:
        at = AutoTrader()
        at._running = True
        at._symboles = ["AAA", "HHH", "FFF"]
        at._cycle_analyse(gen=at._generation)

        motifs = {a["symbole"]: a["motif"] for a in at._abstentions_recentes()}
        # AAA a été tradé → aucun motif à afficher
        assert "AAA" not in motifs, motifs
        # HHH : pas de consensus ; FFF : marché fermé
        assert "consensus" in motifs.get("HHH", ""), motifs
        assert "fermé" in motifs.get("FFF", ""), motifs

        # Deuxième cycle : AAA tombe sous le coup du cooldown, et le dit.
        at._cycle_analyse(gen=at._generation)
        motifs = {a["symbole"]: a["motif"] for a in at._abstentions_recentes()}
        assert "ré-entrée" in motifs.get("AAA", ""), motifs

        # Les motifs remontent dans le statut lu par l'interface.
        statut = at.get_status()
        assert {a["symbole"] for a in statut["abstentions"]} == {"AAA", "HHH", "FFF"}, statut
    finally:
        (_mm.get_mt5_manager, _rg.get_risk_guard, _mh.marche_ouvert,
         _bm.get_orchestrateur) = _o
    print("  OK — chaque abstention est expliquée et remonte au tableau de bord")


def test_objectif_en_cache():
    """Le suivi de l'objectif ne doit pas interroger le courtier à chaque
    sondage du tableau de bord (toutes les 3 s) : il prend le VERROU du
    terminal MetaTrader, celui-là même dont l'envoi d'ordre a besoin."""
    _isoler_config()
    import utils.mt5_manager as _mm
    import utils.auto_trader as _at
    from utils import trading_config as tc

    tc.forcer_en_memoire(objectif_journalier=80.0, capital_reference=100.0)
    _at._objectif_cache["ts"] = 0.0
    _at._objectif_cache["valeur"] = None
    appels = {"n": 0}

    class _Faux:
        def get_historique_reel(self, jours=1):
            appels["n"] += 1
            return {"synced": True, "devise": "EUR", "par_jour": []}
        def get_positions(self):
            return []

    _orig = _mm.get_mt5_manager
    _mm.get_mt5_manager = lambda: _Faux()
    try:
        for _ in range(10):
            _at.objectif_du_jour()
        assert appels["n"] == 1, f"{appels['n']} interrogations du courtier au lieu d'une"
        _at.objectif_du_jour(force=True)
        assert appels["n"] == 2, appels
    finally:
        _mm.get_mt5_manager = _orig
        _at._objectif_cache["ts"] = 0.0
        _at._objectif_cache["valeur"] = None
    print("  OK — objectif mis en cache : le courtier n'est plus sondé en boucle")


def test_cours_du_courtier():
    """Les cours viennent du COURTIER quand un compte est connecté.

    Régression majeure corrigée ici : les prix ne venaient QUE de Yahoo
    (yfinance). Dès que Yahoo tombait (403, 429, quota), l'application
    fabriquait des cours de synthèse marqués « simulés » — et le garde-fou
    « aucun ordre réel sur des prix fictifs » refusait ENSUITE chaque ordre.
    Plus rien ne partait tant que Yahoo ne revenait pas, alors que le courtier
    diffusait ses prix normalement.
    """
    _isoler_config()
    import sys, types
    import utils.mt5_manager as _mm
    from utils.market_data import FetcheurDonnees

    import numpy as np
    faux = types.ModuleType("MetaTrader5")
    faux.TIMEFRAME_H1, faux.TIMEFRAME_M15, faux.TIMEFRAME_D1 = 16385, 15, 16408
    base = 61000.0
    # MetaTrader5 renvoie un tableau NumPy STRUCTURÉ (champs nommés) : on
    # reproduit exactement cette forme, sinon le test validerait un format que
    # la librairie réelle ne produit jamais.
    dtype = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
             ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
             ("real_volume", "<u8")]
    faux.copy_rates_from_pos = lambda sym, tf, dep, n: np.array(
        [(1_700_000_000 + i * 3600, base + i, base + i + 40, base + i - 40,
          base + i + 10, 1200 + i, 2, 0) for i in range(n)], dtype=dtype)

    class _Gest:
        _lib_available = True
        verrou = __import__("threading").RLock()
        def get_status(self): return {"connected": True}
        def resoudre_symbole(self, s): return "BTCUSD"

    _o_mod = sys.modules.get("MetaTrader5")
    _o_gest = _mm.get_mt5_manager
    sys.modules["MetaTrader5"] = faux
    _mm.get_mt5_manager = lambda: _Gest()
    try:
        md = FetcheurDonnees.depuis_mt5("BTC-USD", "1h")
        assert md is not None, "cours du courtier non lus"
        assert len(md.bougies) == FetcheurDonnees.NB_BOUGIES_MT5, len(md.bougies)
        assert md.indicateurs.get("source") == "MT5", md.indicateurs
        # PAS marqué « simulé » : c'est la condition pour qu'un ordre parte.
        assert not md.indicateurs.get("simule"), md.indicateurs
        assert md.prix_actuel > 0

        # obtenir_donnees() doit PRÉFÉRER le courtier — sans toucher à yfinance.
        import utils.market_data as _md
        appels_yahoo = {"n": 0}

        class _TickerKO:
            def __init__(self, *a, **k): appels_yahoo["n"] += 1
            def history(self, **k): raise AssertionError("yfinance ne doit pas être appelé")
        _o_yf = _md.yf.Ticker
        _md.yf.Ticker = _TickerKO
        try:
            md2 = FetcheurDonnees.obtenir_donnees("BTC-USD", "1h")
            assert md2 is not None and md2.indicateurs.get("source") == "MT5", md2.indicateurs
            assert appels_yahoo["n"] == 0, "Yahoo interrogé alors que le courtier répond"
        finally:
            _md.yf.Ticker = _o_yf

        # Compte NON connecté → None, l'appelant retombe sur yfinance.
        class _GestKO(_Gest):
            def get_status(self): return {"connected": False}
        _mm.get_mt5_manager = lambda: _GestKO()
        assert FetcheurDonnees.depuis_mt5("BTC-USD", "1h") is None
    finally:
        _mm.get_mt5_manager = _o_gest
        if _o_mod is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = _o_mod
    print("  OK — cours lus chez le courtier (plus de blocage quand Yahoo tombe)")


def test_reprise_apres_suspension():
    """« Démarrer » sur un robot suspendu doit REPRENDRE, pas répondre
    « déjà en cours » — sinon l'utilisateur est enfermé : le bouton affiche
    « Arrêter », « Démarrer » échoue, et plus aucun ordre ne part."""
    _isoler_config()
    import utils.risk_guard as _rg
    import utils.mt5_manager as _mm
    from utils.auto_trader import AutoTrader

    class _Risk:
        def __init__(self): self.rearme = False
        def reset_kill(self): self.rearme = True; return {"success": True}

    class _Mt5:
        def __init__(self): self.auto = None
        def set_auto_trading(self, v): self.auto = v

    risk, mt5 = _Risk(), _Mt5()
    _o1, _o2 = _rg.get_risk_guard, _mm.get_mt5_manager
    _rg.get_risk_guard = lambda: risk
    _mm.get_mt5_manager = lambda: mt5
    try:
        at = AutoTrader()
        at._running = True
        at._trading_suspendu = True
        at._raison_suspension = "Kill-switch : perte du jour 52% ≥ limite 50%"

        r = at.start(["BTC-USD"], 60)
        assert r["success"] and r.get("reprise"), r
        assert not at._trading_suspendu and not at._raison_suspension
        assert risk.rearme, "kill-switch non réarmé à la reprise"
        assert mt5.auto is True, "trading automatique non réactivé chez le courtier"

        # Robot déjà actif ET non suspendu : le message d'origine est conservé.
        r2 = at.start(["BTC-USD"], 60)
        assert not r2["success"] and "déjà en cours" in r2["error"], r2
    finally:
        _rg.get_risk_guard, _mm.get_mt5_manager = _o1, _o2
    print("  OK — « Démarrer » relance un robot suspendu au lieu de le bloquer")


def test_reglages_survivent_au_redemarrage():
    """Un réglage fait dans l'interface doit SURVIVRE au redémarrage, même
    quand une variable d'environnement porte le même nom.

    Régression vécue : l'utilisateur montait « positions max » à 50 dans
    l'écran Réglages, l'application confirmait et l'enregistrait — puis, au
    redémarrage, un `MAX_POSITIONS=10` oublié dans un vieux `.env` reprenait
    la main SANS RIEN DIRE. Le plafond bas était atteint dès les premières
    positions et plus aucun ordre ne partait.
    """
    _isoler_config()
    import os
    import utils.risk_guard as _rg
    from utils import trading_config as tc

    _env = {c: os.environ.get(c) for c in ("MAX_POSITIONS", "EXPOSITION_MAX_PCT",
                                           "PERTE_MAX_JOURNALIERE_PCT", "LEVIER_MAX")}
    os.environ["MAX_POSITIONS"] = "10"
    os.environ["EXPOSITION_MAX_PCT"] = "50"
    os.environ["LEVIER_MAX"] = "3"
    try:
        # 1. Sans réglage utilisateur, l'environnement fournit la valeur initiale.
        _rg._guard = None
        g0 = _rg.RiskGuard(persister=True)
        assert g0.max_positions == 10 and g0.exposition_max_pct == 50.0, vars(g0)

        # 2. L'utilisateur règle depuis l'interface, et cela s'enregistre.
        r = g0.configurer({"max_positions": 50, "exposition_max_pct": 1000})
        assert r["success"], r

        # 3. REDÉMARRAGE : une instance neuve relit le disque. Le réglage de
        #    l'utilisateur l'emporte sur la variable d'environnement.
        g1 = _rg.RiskGuard(persister=True)
        assert g1.max_positions == 50, f"réglage écrasé par l'environnement : {g1.max_positions}"
        assert g1.exposition_max_pct == 1000.0, g1.exposition_max_pct

        # Même règle côté moteur de performance (déjà en place, on la verrouille).
        tc.recharger()
        assert tc.get("levier_max") == 3.0, "valeur initiale d'environnement ignorée"
        assert tc.configurer({"levier_max": 10})["success"]
        tc.recharger()
        assert tc.get("levier_max") == 10.0, "réglage d'interface écrasé au redémarrage"
    finally:
        for cle, val in _env.items():
            if val is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = val
        _rg._guard = None
        tc.forcer_en_memoire()
    print("  OK — réglages de l'interface prioritaires et persistants au redémarrage")


def test_api_performance():
    """Les endpoints de réglage : lecture, écriture bornée, entrées malformées."""
    _isoler_config()
    os.environ["APP_AUTH"] = "off"
    from fastapi.testclient import TestClient
    from backend.main import app
    from utils import trading_config as tc
    tc.forcer_en_memoire()

    client = TestClient(app, raise_server_exceptions=False)

    d = client.get("/api/trading/config").json()
    assert d["config"]["levier_max"] == 10.0, d

    r = client.post("/api/trading/config", json={"levier_max": 4, "levier": 9})
    assert r.status_code == 200 and r.json()["success"], r.text
    assert r.json()["config"]["levier"] == 4.0, r.json()      # rabattu au plafond

    # Levier réglé DEPUIS L'INTERFACE : enregistré, donc il tient.
    r = client.post("/api/trading/levier", json={"levier": 2, "source": "interface"})
    assert r.json()["success"] and tc.get("levier") == 2.0, r.json()

    # Aucune entrée malformée ne doit produire un 500.
    for corps in (None, [], "texte", 42, {"levier_max": "abc"}, {"levier": {}},
                  {"levier": None}, {"cooldown_s": []}):
        for url in ("/api/trading/config", "/api/trading/levier"):
            rep = client.post(url, json=corps)
            assert rep.status_code < 500, (url, corps, rep.status_code, rep.text)

    assert client.get("/api/objectif").status_code == 200
    print("  OK — API : /api/trading/config, /levier, /objectif (aucun 500)")


def run():
    print("═══ Moteur de performance ═══")
    test_bornes_et_tout_ou_rien()
    test_levier_agents()
    test_volume_au_levier()
    test_fermeture_globale()
    test_objectif_du_jour()
    test_cycle_transporte_levier_et_filtres()
    test_motifs_abstention()
    test_objectif_en_cache()
    test_cours_du_courtier()
    test_reprise_apres_suspension()
    test_reglages_survivent_au_redemarrage()
    test_api_performance()


if __name__ == "__main__":
    run()
    print("✅ Moteur de performance OK")
