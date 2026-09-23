"""Tests des fonctionnalités applicatives :
- Notifications push mobile (ntfy) en plus du webhook Discord/Slack
- Multi-comptes AvaTrade (mémoriser / lister / basculer / supprimer)
- Export CSV de l'historique des trades
- Réactivité du serveur, robustesse des indicateurs, fermeture en simulation,
  bandeau d'erreurs et échappement HTML du tableau de bord
"""
import _setup  # noqa: F401
import json
import os
import tempfile
from pathlib import Path


def _isoler_config():
    """Redirige la config vers un chemin temporaire inexistant (n'écrase
    jamais la vraie config de l'utilisateur)."""
    from utils import app_config
    fd, chemin = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(chemin)                      # on veut un chemin inexistant au départ
    app_config._PATH = Path(chemin)
    app_config._cache = None


def test_notifier_ntfy():
    _isoler_config()
    from utils import notifier
    # rien de configuré au départ
    assert not notifier.canaux_configures()
    # webhook seul → canal configuré
    assert notifier.set_webhook("https://discord.com/api/webhooks/x")
    assert notifier.canaux_configures()
    # ntfy : nom de sujet → URL ntfy.sh
    notifier.set_ntfy("mon-sujet-secret")
    assert notifier.get_ntfy() == "mon-sujet-secret"
    assert notifier._ntfy_url() == "https://ntfy.sh/mon-sujet-secret"
    # URL complète (serveur auto-hébergé) respectée telle quelle
    notifier.set_ntfy("https://ntfy.example.com/topic")
    assert notifier._ntfy_url() == "https://ntfy.example.com/topic"
    # désactivation
    notifier.set_ntfy("")
    assert notifier._ntfy_url() == ""
    print("  OK — ntfy : sujet → URL, URL complète respectée, canaux cumulables")


def test_webhook_https_obligatoire():
    """Le webhook transporte les ordres passés et l'état du compte : http:// est
    refusé, comme pour le sujet ntfy (secret en clair + POST vers une adresse
    arbitraire du réseau interne)."""
    _isoler_config()
    from utils import notifier
    assert notifier.set_webhook("https://discord.com/api/webhooks/x")
    assert notifier.get_webhook() == "https://discord.com/api/webhooks/x"
    for mauvais in ("http://discord.com/api/webhooks/x",
                    "http://192.168.1.1/admin",
                    "httpx://exemple",
                    "ftp://exemple/x"):
        assert not notifier.set_webhook(mauvais), f"{mauvais} accepté !"
    # l'URL valide précédente n'a pas été écrasée par un refus
    assert notifier.get_webhook() == "https://discord.com/api/webhooks/x"
    assert notifier.set_webhook("")            # vide = désactivation
    assert not notifier.canaux_configures()
    print("  OK — webhook : https obligatoire, refus sans écrasement")


def test_purge_base_retention():
    """La base était purgée NULLE PART : nettoyer_anciens_signaux() existait
    mais n'était jamais appelée. Un cycle écrit 46 signaux PAR SYMBOLE — une
    instance laissée tourner saturait le disque."""
    import datetime as _dt
    from utils.database import Database
    import utils.auto_trader as at

    fd, chemin = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(chemin)
    db = Database(Path(chemin))
    try:
        vieux = (_dt.datetime.now() - _dt.timedelta(days=90)).isoformat()
        recent = _dt.datetime.now().isoformat()
        for ts in (vieux, vieux, recent):
            db.sauver_signal({"agent_id": "A1", "agent_nom": "Test",
                              "symbole": "BTC-USD", "action": "HOLD",
                              "confiance": 50, "timestamp": ts})
        assert len(db.lire_signaux(100)) == 3
        assert db.nettoyer_anciens_signaux(30) == 2
        restants = db.lire_signaux(100)
        assert len(restants) == 1 and restants[0]["timestamp"] == recent

        # purger_base() est bien câblée à la boucle : une fois par jour, et
        # désactivable par RETENTION_JOURS=0.
        assert at.purger_base(0) == 0
        trader = at.AutoTrader()
        appels = []
        origine = at.purger_base
        at.purger_base = lambda *a, **k: appels.append(1)
        try:
            trader._purge_quotidienne_si_due()
            trader._purge_quotidienne_si_due()   # même jour → une seule fois
            import time as _t
            _t.sleep(0.2)                        # la purge tourne en thread
        finally:
            at.purger_base = origine
        assert len(appels) == 1, appels
    finally:
        Path(chemin).unlink(missing_ok=True)
    print("  OK — purge : signaux périmés supprimés, une seule fois par jour")


def test_env_virgule_decimale():
    """Une variable saisie « 0,5 » ne doit pas faire sauter EN SILENCE les
    contrôles de pré-vol (lot minimum, spread, perte au stop)."""
    from utils import preflight
    for brut, attendu in (("0,5", 0.5), ("1.25", 1.25), ("abc", 9.0), (None, 9.0)):
        if brut is None:
            os.environ.pop("SPREAD_MAX_PCT", None)
        else:
            os.environ["SPREAD_MAX_PCT"] = brut
        assert preflight._env_float("SPREAD_MAX_PCT", 9.0) == attendu, brut
    os.environ.pop("SPREAD_MAX_PCT", None)

    # Plus aucun `float(os.getenv(...))` nu dans le module : ces conversions
    # levaient ValueError et le diagnostic retombait sur « non simulable »,
    # sautant en silence le contrôle. Analyse AST (et non recherche de texte) :
    # la prose et les docstrings ne doivent pas déclencher de faux positif.
    import ast
    arbre = ast.parse(Path(preflight.__file__).read_text(encoding="utf-8"))
    nus = [
        n.lineno for n in ast.walk(arbre)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "float"
        and n.args
        and isinstance(n.args[0], ast.Call)
        and getattr(n.args[0].func, "attr", None) == "getenv"
    ]
    assert not nus, (
        f"preflight.py lignes {nus} : float(os.getenv(...)) non protégé — "
        f"une virgule décimale désactiverait silencieusement un contrôle")
    print("  OK — variables d'environnement : virgule décimale tolérée partout")


def test_multi_comptes():
    _isoler_config()
    from utils import comptes, app_config
    # vide au départ
    assert comptes.lister()["comptes"] == []
    # mémoriser un compte MT5 → apparaît, devient actif, id stable
    comptes.memoriser_mt5("123456", "secret42", "real", "Ava-Real 1-MT5")
    liste = comptes.lister()
    assert len(liste["comptes"]) == 1
    c = liste["comptes"][0]
    assert c["type"] == "mt5" and c["actif"] and c["id"] == "mt5:123456"
    # le secret ne doit JAMAIS apparaître en clair dans la config
    brut = json.dumps(app_config.get("comptes"))
    assert "secret42" not in brut, "mot de passe en clair dans la config !"
    # 2e compte MT5 → 2 comptes, le nouveau devient actif, secret obfusqué
    comptes.memoriser_mt5("987654", "autreSecret9", "demo", "Ava-Demo 1-MT5")
    liste = comptes.lister()
    assert len(liste["comptes"]) == 2
    assert liste["actif"] == "mt5:987654"
    assert "autreSecret9" not in json.dumps(app_config.get("comptes"))
    # re-mémoriser le même MT5 → pas de doublon (upsert par clé stable)
    comptes.memoriser_mt5("123456", "secret42", "real", "Ava-Real 1-MT5")
    assert len(comptes.lister()["comptes"]) == 2
    # renommer
    assert comptes.renommer("mt5:123456", "Mon compte réel")["success"]
    labels = {c["id"]: c["label"] for c in comptes.lister()["comptes"]}
    assert labels["mt5:123456"] == "Mon compte réel"
    # ré-mémoriser le 1er MT5 l'a rendu actif → supprimer le 2e (non actif)
    # laisse l'actif inchangé
    comptes.supprimer("mt5:987654")
    ids = [c["id"] for c in comptes.lister()["comptes"]]
    assert ids == ["mt5:123456"]
    assert app_config.get("compte_actif") == "mt5:123456"
    # supprimer le compte ACTIF → liste vide + plus aucun compte actif
    comptes.supprimer("mt5:123456")
    assert comptes.lister()["comptes"] == []
    assert app_config.get("compte_actif") == ""
    print("  OK — multi-comptes : mémo/liste/upsert/renomme/suppr, secrets obfusqués")


def test_aucun_code_demande_par_defaut():
    """L'application s'ouvre sans rien demander — et sait le redevenir.

    Choix explicite du projet : plus de code d'accès au lancement. Le
    mécanisme entier reste en place, et c'est ce que ce test protège des deux
    côtés : ni un code qui réapparaît sans qu'on l'ait demandé, ni un
    `APP_AUTH=on` qui n'aurait plus d'effet.

    `APP_PASSWORD` compte comme une demande d'authentification : imposer un
    code sans qu'il soit jamais demandé n'aurait aucun sens, et la variable
    deviendrait silencieusement inopérante.
    """
    import importlib
    import os as _os
    from utils import auth

    sauvegarde = {c: _os.environ.get(c) for c in ("APP_AUTH", "APP_PASSWORD")}
    try:
        for cas, attendu in (
            ({},                                    False),   # par défaut
            ({"APP_AUTH": "on"},                    True),
            ({"APP_AUTH": "oui"},                   True),
            ({"APP_AUTH": "off"},                   False),
            ({"APP_PASSWORD": "1234"},              True),    # implique l'activation
            # Le réglage explicite prime sur le mot de passe imposé.
            ({"APP_AUTH": "off", "APP_PASSWORD": "1234"}, False),
        ):
            for c in ("APP_AUTH", "APP_PASSWORD"):
                _os.environ.pop(c, None)
            _os.environ.update(cas)
            importlib.reload(auth)
            assert auth.auth_active() is attendu, (
                f"auth_active() = {auth.auth_active()} avec {cas or 'aucune variable'}, "
                f"attendu {attendu}")
    finally:
        for c, v in sauvegarde.items():
            if v is None:
                _os.environ.pop(c, None)
            else:
                _os.environ[c] = v
        importlib.reload(auth)


def test_serveur_public_sans_code_lui_aussi():
    """`server.py` n'impose plus de code : le choix vaut partout.

    Une version précédente y reposait `APP_AUTH=on`, au motif que ce fichier
    sert une URL publique (Railway / Render, via le Procfile) et non le poste
    de son propriétaire. Ce n'est plus le cas : le projet ne demande de code
    nulle part, et ce test empêche la protection de revenir par inadvertance
    au détour d'un correctif.

    Ce qui RESTE exigé : que le démarrage écrive dans le journal si un code
    est demandé ou non. Sur un serveur, c'est la seule trace qui permette de
    constater l'état réel sans aller lire le code source.
    """
    racine = Path(__file__).resolve().parents[2]
    source = (racine / "server.py").read_text(encoding="utf-8")

    assert 'setdefault("APP_AUTH"' not in source, (
        "server.py réimpose un code d'accès : le déploiement en demanderait un")
    assert 'os.environ["APP_AUTH"]' not in source, (
        "server.py force APP_AUTH")

    assert "_annoncer_authentification" in source, (
        "le démarrage n'indique plus si un code est demandé — sur un serveur, "
        "c'est la seule façon de le constater")

    # Le garde-fou doit porter sur le point d'entrée réellement lancé.
    procfile = (racine / "Procfile").read_text(encoding="utf-8")
    assert "server.py" in procfile, "le Procfile ne lance plus server.py"


def test_interface_sait_si_un_code_existe():
    """Les réglages n'affichent « changer le code » que s'il y en a un.

    Sans cette information, la fenêtre de réglages proposerait de changer un
    code inexistant et de fermer une session qui n'existe pas : deux boutons
    qui ne peuvent répondre que par une erreur.
    """
    racine = Path(__file__).resolve().parent.parent.parent
    source = (racine / "backend" / "main.py").read_text(encoding="utf-8")
    assert 'reponse["auth"] = auth_active()' in source, (
        "/api/status n'expose plus l'état de l'authentification")

    outils = (racine / "frontend" / "js" / "tools.js").read_text(encoding="utf-8")
    assert "toolsLoadSecurite" in outils, (
        "l'interface ne s'adapte plus à l'absence de code d'accès")
    assert "sec-sans-code" in outils and "sec-avec-code" in outils


def test_securite_mot_de_passe():
    """KDF lent + salé, migration depuis l'ancien SHA-256, invalidation des
    sessions au changement de code, anti-force-brute PAR IP."""
    _isoler_config()
    import importlib
    from utils import auth
    importlib.reload(auth)

    # 1. Le hash stocké est au format PBKDF2 salé (pas un SHA-256 nu)
    assert auth.changer_mot_de_passe("123456")
    from utils import app_config
    stocke = app_config.get("app_password_hash")
    assert stocke.startswith("pbkdf2$"), stocke
    assert "123456" not in stocke
    # Deux hachages du MÊME mot de passe diffèrent (sel aléatoire)
    assert auth._hash("123456") != auth._hash("123456")
    assert auth.verifier_mot_de_passe("123456")
    assert not auth.verifier_mot_de_passe("123457")

    # 2. Migration transparente d'un hash hérité (SHA-256 non salé)
    app_config.set("app_password_hash", auth._hash_legacy("497040"))
    assert auth.verifier_mot_de_passe("497040")          # accepté
    migre = app_config.get("app_password_hash")
    assert migre.startswith("pbkdf2$"), "hash hérité non migré"
    assert auth.verifier_mot_de_passe("497040")          # toujours valide après migration

    # 3. Changer le code invalide les sessions existantes (rotation du secret)
    jeton = auth.creer_token(30)
    assert auth.token_valide(jeton)
    assert auth.changer_mot_de_passe("nouveau42")
    assert not auth.token_valide(jeton), "session non invalidée après changement de code"
    assert auth.token_valide(auth.creer_token(30))       # un nouveau jeton marche

    # 4. Anti-force-brute PAR IP : bloquer une IP n'affecte pas les autres
    os.environ["LOGIN_MAX_ECHECS"] = "3"
    os.environ["LOGIN_BLOCAGE_S"] = "2"
    importlib.reload(auth)
    auth.reinitialiser_echecs()
    for _ in range(3):
        auth.enregistrer_echec("10.0.0.9")
    assert auth.login_bloque("10.0.0.9")[0], "IP fautive non bloquée"
    assert not auth.login_bloque("10.0.0.1")[0], "une autre IP est bloquée (DoS du propriétaire)"
    auth.reinitialiser_echecs("10.0.0.9")
    assert not auth.login_bloque("10.0.0.9")[0]
    os.environ.pop("LOGIN_MAX_ECHECS", None)
    os.environ.pop("LOGIN_BLOCAGE_S", None)
    importlib.reload(auth)
    print("  OK — mot de passe : PBKDF2 salé, migration, sessions invalidées, blocage par IP")


def test_ntfy_validation():
    """Le sujet ntfy ne doit pas permettre un POST serveur vers une adresse
    arbitraire (SSRF) ni du HTTP en clair."""
    _isoler_config()
    from utils import notifier
    assert notifier.set_ntfy("mon-sujet_42")                    # nom simple : OK
    assert notifier.set_ntfy("https://ntfy.exemple.fr/sujet")   # https : OK
    assert not notifier.set_ntfy("http://192.168.1.1/admin")    # http en clair : refusé
    assert not notifier.set_ntfy("file:///etc/passwd")          # schéma exotique : refusé
    assert not notifier.set_ntfy("sujet/../../interne")         # chemin douteux : refusé
    assert not notifier.set_ntfy("sujet avec espaces")          # caractères invalides
    assert notifier.set_ntfy("")                                # désactivation : OK
    print("  OK — ntfy : validation du sujet (anti-SSRF, https imposé)")


def test_kill_switch_persistant():
    _isoler_config()
    from utils.risk_guard import RiskGuard
    # Jour en cours : équité d'ouverture 10000, puis perte de 4% (sous la limite).
    # Limite fixée EXPLICITEMENT : le défaut du garde-fou est un réglage
    # utilisateur (50 %), ce test vérifie le mécanisme, pas la valeur livrée.
    g = RiskGuard(persister=True)
    g.perte_max_pct = 5.0
    ok, _ = g.evaluer({"equity": 10000, "nb_positions": 0, "exposition_pct": 0})
    assert ok
    ok, _ = g.evaluer({"equity": 9600, "nb_positions": 0, "exposition_pct": 0})
    assert ok   # -4%, sous la limite de 5%
    # Redémarrage en cours de séance : un nouveau garde-fou persistant DOIT
    # restaurer l'équité d'ouverture (10000), et non repartir de 9600 dégradé.
    g2 = RiskGuard(persister=True)
    g2.perte_max_pct = 5.0
    assert g2._equity_debut == 10000, g2._equity_debut
    # -6% cumulé depuis 10000 → kill-switch (et non un budget neuf depuis 9600)
    ok, raison = g2.evaluer({"equity": 9400, "nb_positions": 0, "exposition_pct": 0})
    assert not ok and "Kill-switch" in raison, (ok, raison)
    # Le kill persiste aussi au redémarrage
    g3 = RiskGuard(persister=True)
    g3.perte_max_pct = 5.0
    ok, _ = g3.evaluer({"equity": 9400, "nb_positions": 0, "exposition_pct": 0})
    assert not ok
    print("  OK — kill-switch : équité d'ouverture + kill persistent au redémarrage (B6)")


def test_arret_et_suspension():
    """Régressions de la revue : (a) un arrêt demandé pendant un cycle stoppe
    les ordres restants ; (b) le kill-switch suspend les OUVERTURES mais laisse
    vivre la surveillance des positions."""
    _isoler_config()
    import utils.mt5_manager as _mm
    from utils.auto_trader import AutoTrader

    # (a) arrêt en cours de cycle : les symboles restants ne sont pas tradés
    at = AutoTrader()
    ordres = []

    class _FakeMt5:
        _lib_available = True
        def est_reel(self): return False
        def get_status(self): return {"connected": True, "auto_trading": True}
        def get_portfolio(self): return {"equity": 10000, "nb_positions": 0, "exposition_pct": 0}
        def directions_ouvertes(self, s): return []
        def resoudre_symbole(self, s): return s
        def get_positions(self): return []
        def get_journal(self, n=20): return []
        def set_auto_trading(self, v): pass
        def execute_trade(self, sig):
            ordres.append(sig["symbol"])
            at._running = False          # l'utilisateur clique « Arrêter »
            return {"success": True, "price": 1.0}

    class _RiskOK:
        def evaluer(self, _p): return (True, "")

    faux = _FakeMt5()
    _o1 = _mm.get_mt5_manager
    _mm.get_mt5_manager = lambda: faux
    import utils.risk_guard as _rg
    _o2 = _rg.get_risk_guard
    _rg.get_risk_guard = lambda: _RiskOK()
    # marché toujours ouvert + décisions sur 3 symboles
    import utils.market_hours as _mh
    _o3 = _mh.marche_ouvert
    _mh.marche_ouvert = lambda s: (True, "")
    import backend.main as _bm
    _o4 = _bm.get_orchestrateur

    class _Chef:
        def orchestrer(self, syms):
            return {"decisions": {s: {"action": "BUY", "confiance": 60,
                                      "prix_entree": 100.0} for s in syms}}
    _bm.get_orchestrateur = lambda: _Chef()
    try:
        at._running = True
        at._symboles = ["AAA", "BBB", "CCC"]
        at._cycle_analyse(gen=at._generation)
        assert len(ordres) == 1, f"cycle non interrompu : {ordres}"
    finally:
        _mm.get_mt5_manager, _rg.get_risk_guard = _o1, _o2
        _mh.marche_ouvert, _bm.get_orchestrateur = _o3, _o4
    print("  OK — arrêt pendant un cycle : ordres restants annulés")

    # (b) kill-switch : suspend les ouvertures, la boucle reste vivante
    at2 = AutoTrader()
    at2._running = True

    class _RiskKill:
        def evaluer(self, _p):
            return (False, "Kill-switch : perte du jour 6.0% ≥ limite 5.0%")

    _mm.get_mt5_manager = lambda: faux
    _rg.get_risk_guard = lambda: _RiskKill()
    _mh.marche_ouvert = lambda s: (True, "")
    _bm.get_orchestrateur = lambda: _Chef()
    try:
        at2._symboles = ["AAA"]
        at2._cycle_analyse(gen=at2._generation)
        assert at2._trading_suspendu, "kill-switch n'a pas suspendu les ouvertures"
        assert at2._running, "la surveillance des positions a été coupée par le kill-switch"
        assert at2.get_status()["suspendu"] is True
    finally:
        _mm.get_mt5_manager, _rg.get_risk_guard = _o1, _o2
        _mh.marche_ouvert, _bm.get_orchestrateur = _o3, _o4
    print("  OK — kill-switch : ouvertures suspendues, surveillance maintenue")


def test_preflight():
    """La vérification pré-vol rejoue la décision SANS envoyer d'ordre et
    rapporte volume / stop-loss / perte au stop réellement applicables."""
    _isoler_config()
    import utils.mt5_manager as _mm
    import utils.market_data as _md
    from models.market_data import MarketData
    import pandas as pd

    envoyes = []

    class _FauxMt5:
        _lib_available = False           # chemin sans librairie MT5
        def est_reel(self): return False
        def equity_live(self): return 10000.0
        def get_status(self):
            return {"connected": True,
                    "account_info": {"account": "42", "server": "Ava-Demo",
                                     "currency": "USD", "simulated": True}}
        def resoudre_symbole(self, s): return s
        def execute_trade(self, sig):    # ne DOIT jamais être appelé
            envoyes.append(sig)
            return {"success": True}

    def _md_reelle(*a, **k):
        n = 80
        closes = [100.0 + i * 0.1 for i in range(n)]
        df = pd.DataFrame({"Open": closes, "High": [c * 1.001 for c in closes],
                           "Low": [c * 0.999 for c in closes], "Close": closes,
                           "Volume": [1e6] * n},
                          index=pd.date_range(end="2025-01-01", periods=n, freq="h"))
        return MarketData.depuis_dataframe("BTC-USD", "1h", df)

    _o1, _o2 = _mm.get_mt5_manager, _md.FetcheurDonnees.obtenir_donnees
    _mm.get_mt5_manager = lambda: _FauxMt5()
    _md.FetcheurDonnees.obtenir_donnees = staticmethod(_md_reelle)
    try:
        from utils.preflight import verifier
        r = verifier(["BTC-USD"])
        assert not envoyes, "la vérification a ENVOYÉ un ordre !"
        assert r["verdict"] in ("pret", "avertissement", "bloquant")
        s = r["symboles"][0]
        assert s["symbole"] == "BTC-USD"
        assert s["prix"] > 0, s
        # Le stop-loss par défaut (2%) doit être sous le prix pour un achat
        assert 0 < s["stop_loss"] < s["prix"], s
        assert s["take_profit"] > s["prix"], s
        # Le compte simulé doit être signalé comme tel
        assert any("simulation" in c["titre"].lower() for c in r["checks"]), r["checks"]

        # AUDIT : sans les specs du courtier, la perte au stop N'EST PAS
        # calculable. Elle ne doit surtout PAS être affichée : le calcul de
        # repli (distance × lots, sans taille de contrat) la sous-évaluait d'un
        # facteur 100 000 sur le Forex — un faux feu vert sur le chiffre même
        # qui justifie ce module.
        assert "perte_au_stop" not in s, \
            f"perte affichée sans specs courtier : {s.get('perte_au_stop')}"
        assert any("NON CALCULABLE" in m for m in s["messages"]), s["messages"]
        assert s["statut"] != "ok", s

        # AUDIT : le statut ne doit JAMAIS être rétrogradé. Une ERREUR posée
        # tôt (données simulées sur compte réel) survit aux ATTENTION suivantes.
        from utils.preflight import _degrader, OK as _OK, ATTENTION as _AT, ERREUR as _ER
        res = {"statut": _OK}
        _degrader(res, _ER);  assert res["statut"] == _ER
        _degrader(res, _AT);  assert res["statut"] == _ER, "ERREUR rétrogradée !"
        res2 = {"statut": _OK}
        _degrader(res2, _AT); assert res2["statut"] == _AT
        _degrader(res2, _ER); assert res2["statut"] == _ER   # élévation OK
    finally:
        _mm.get_mt5_manager = _o1
        _md.FetcheurDonnees.obtenir_donnees = _o2
    print("  OK — pré-vol : aucun ordre, perte non affichée sans specs, statut monotone")


def test_encodage_windows_et_suggestions():
    """(a) Une console Windows cp1252 ne doit JAMAIS interrompre un cycle de
    trading (l'erreur « 'charmap' codec can't encode '→' » remontait dans le
    bandeau et stoppait les symboles suivants).
    (b) Un symbole introuvable doit PROPOSER les noms réels du courtier."""
    import io
    import logging as _lg
    import utils.mt5_manager as _mm
    import utils.risk_guard as _rg
    import utils.market_hours as _mh
    import backend.main as _bm
    from utils.auto_trader import AutoTrader

    # (a) cycle complet avec un flux de log strictement cp1252
    flux = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    h = _lg.StreamHandler(flux)
    racine = _lg.getLogger()
    racine.addHandler(h)

    err_courtier = ("Symbole « BTC-USD » indisponible chez votre courtier. "
                    "Ouvrez MetaTrader → Market Watch pour voir les symboles.")

    class _Mt5:
        _lib_available = True
        def est_reel(self): return True
        def get_status(self): return {"connected": True, "auto_trading": True}
        def get_portfolio(self): return {"equity": 10000, "nb_positions": 0, "exposition_pct": 0}
        def directions_ouvertes(self, s): return []
        def resoudre_symbole(self, s): return None
        def get_positions(self): return []
        def get_journal(self, n=20): return []
        def set_auto_trading(self, v): pass
        def execute_trade(self, sig): return {"success": False, "error": err_courtier}

    class _Risk:
        def evaluer(self, p): return (True, "")

    class _Chef:
        def orchestrer(self, syms):
            return {"decisions": {s: {"action": "BUY", "confiance": 54,
                                      "prix_entree": 60000.0} for s in syms}}

    o1, o2, o3, o4 = (_mm.get_mt5_manager, _rg.get_risk_guard,
                      _mh.marche_ouvert, _bm.get_orchestrateur)
    _mm.get_mt5_manager = lambda: _Mt5()
    _rg.get_risk_guard = lambda: _Risk()
    _mh.marche_ouvert = lambda s: (True, "")
    _bm.get_orchestrateur = lambda: _Chef()
    try:
        at = AutoTrader()
        at._running = True
        at._symboles = ["BTC-USD", "AAPL"]
        at._cycle_analyse(gen=at._generation)
        assert not any("charmap" in e for e in at._errors), at._errors
        # les DEUX symboles ont été traités (le cycle n'a pas été interrompu)
        assert sum(1 for e in at._errors if "échoué" in e) == 2, at._errors
    finally:
        racine.removeHandler(h)
        _mm.get_mt5_manager, _rg.get_risk_guard = o1, o2
        _mh.marche_ouvert, _bm.get_orchestrateur = o3, o4
    print("  OK — console cp1252 : cycle non interrompu, aucune erreur 'charmap'")

    # (b) suggestions de symboles réels du courtier
    import sys
    import types
    faux = types.ModuleType("MetaTrader5")
    dispo = ["BTCUSDT.a", "ETHUSDT.a", "EURUSD"]
    faux.symbol_select = lambda n, on: True
    faux.symbol_info = lambda n: types.SimpleNamespace(name=n) if n in dispo else None
    faux.symbols_get = lambda: [types.SimpleNamespace(name=n) for n in dispo]
    ancien = sys.modules.get("MetaTrader5")
    sys.modules["MetaTrader5"] = faux
    try:
        from utils.mt5_manager import MT5Manager
        m = MT5Manager()
        m._lib_available = True
        m._connected = True
        m._account_info = {"equity": 10000, "currency": "USD"}
        m._symbol_cache.clear()
        r = m.execute_trade({"symbol": "BTC-USD", "action": "BUY",
                             "volume": 0.27, "stop_loss": 59000.0})
        assert not r["success"]
        assert "BTCUSDT.a" in r["error"], r["error"]
    finally:
        if ancien is not None:
            sys.modules["MetaTrader5"] = ancien
        else:
            sys.modules.pop("MetaTrader5", None)
    print("  OK — symbole introuvable : noms réels du courtier proposés")


def test_export_csv():
    import utils.database as dbmod

    class _FakeDB:
        def lire_ordres(self, limite):
            return [{
                "horodatage": "2026-07-23T10:00:00", "symbole": "BTC-USD",
                "action": "BUY", "volume": 0.01, "prix": 60000, "ticket": "1",
                "succes": 1, "mode": "reel", "erreur": None,
            }]

    orig = dbmod.Database
    dbmod.Database = lambda *a, **k: _FakeDB()
    try:
        from backend.main import trades_export_csv
        # endpoint synchrone (exécuté en threadpool par FastAPI) — appel direct
        resp = trades_export_csv()
        body = resp.body.decode("utf-8")
        assert "horodatage,symbole,action" in body, body[:120]
        assert "BTC-USD" in body and "reel" in body
        assert "text/csv" in resp.media_type
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert "historique_trades.csv" in resp.headers.get("content-disposition", "")
    finally:
        dbmod.Database = orig
    print("  OK — export CSV : entête + lignes + type text/csv + pièce jointe")


def test_analyse_arriere_plan_non_bloquante():
    """/api/analyser?async_mode=true ne doit PAS figer la boucle d'événements.

    Starlette exécute une tâche de fond `async def` DANS la boucle : comme
    orchestrer() est bloquant (46 agents, une à deux minutes), tout le serveur
    devenait muet — y compris le bouton « Arrêter » — avec des positions
    réelles ouvertes. Déclarée `def`, la tâche part dans le pool de threads.
    """
    import inspect
    from backend.main import _analyser_async
    assert not inspect.iscoroutinefunction(_analyser_async), (
        "_analyser_async est redevenue `async def` : elle bloquerait "
        "la boucle d'événements pendant tout le cycle d'analyse")
    print("  OK — analyse en arrière-plan déportée hors de la boucle d'événements")


def test_indicateurs_volumes_absents():
    """OBV/VWAP ne doivent jamais planter ni tronquer quand les volumes
    manquent : yfinance n'en renvoie pas toujours et l'orchestrateur passe
    `volumes: []` quand les données de marché sont indisponibles."""
    from utils.indicators import Indicateurs
    closes = [10.0, 11.0, 10.5, 12.0, 12.0]
    highs  = [c + 1 for c in closes]
    lows   = [c - 1 for c in closes]

    # volumes VIDES : avant, obv() levait IndexError sur volumes[0] et l'agent
    # retombait silencieusement en signal neutre.
    obv = Indicateurs.obv(closes, [])
    assert len(obv) == len(closes), obv
    vwap = Indicateurs.vwap(highs, lows, closes, [])
    assert len(vwap) == len(closes), vwap
    assert vwap[-1] == closes[-1]          # sans volume, VWAP retombe sur le prix

    # volumes PLUS COURTS que les clôtures : le zip tronquait la sortie et les
    # appelants lisaient un vwap[-1] périmé (ou plantaient sur liste vide).
    obv2 = Indicateurs.obv(closes, [100.0, 200.0])
    assert len(obv2) == len(closes), obv2
    vwap2 = Indicateurs.vwap(highs, lows, closes, [100.0, 200.0])
    assert len(vwap2) == len(closes), vwap2

    # volumes complets : le calcul reste celui d'origine
    vols = [100.0, 200.0, 150.0, 300.0, 250.0]
    obv3 = Indicateurs.obv(closes, vols)
    assert obv3[1] == obv3[0] + 200.0      # hausse → +volume
    assert obv3[2] == obv3[1] - 150.0      # baisse → -volume
    assert obv3[4] == obv3[3]              # prix plat → inchangé
    print("  OK — OBV/VWAP : volumes absents ou partiels, longueur préservée")


def test_fermeture_position_simulee():
    """Le bouton « Fermer » doit fonctionner AUSSI en simulation.

    Les positions simulées dérivent du journal ; fermer_ticket() ne regardait
    que MetaTrader et répondait toujours « position introuvable ».
    """
    from utils.mt5_manager import MT5Manager
    m = MT5Manager()
    m._lib_available = False
    m._connect_simulated(99999999, "motdepasse", "Simulation", "demo")
    r = m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.05,
                         "prix_entree": 60000.0})
    assert r["success"], r
    ticket = r["ticket"]
    assert any(p["ticket"] == ticket for p in m.get_positions()), m.get_positions()

    # ticket en CHAÎNE (comme envoyé par le frontend en JSON) : doit marcher
    fermeture = m.fermer_ticket(str(ticket))
    assert fermeture["success"], fermeture
    assert not any(p["symbol"] == "BTC-USD" for p in m.get_positions()), \
        "position toujours affichée comme ouverte après fermeture"
    assert m.fermer_ticket(999999999)["success"] is False   # ticket inconnu
    print("  OK — fermeture d'une position simulée par ticket")


def test_bandeau_erreurs_non_inonde():
    """Une panne répétée ne doit pas chasser les autres erreurs du bandeau.

    La surveillance des positions tourne toutes les 5 s : un courtier
    injoignable remplissait les 20 lignes avec le MÊME message en moins de
    deux minutes, masquant la raison d'un ordre refusé."""
    from utils.auto_trader import AutoTrader
    at = AutoTrader()
    at._ajouter_erreur("Ordre bloqué (sécurité) : plafond atteint")
    for _ in range(40):
        at._ajouter_erreur("Surveillance positions : courtier injoignable")
    assert len(at._errors) == 2, at._errors
    assert "courtier injoignable" in at._errors[0]
    assert "plafond atteint" in at._errors[1], "erreur utile chassée du bandeau"
    print("  OK — bandeau d'erreurs : répétitions fusionnées, historique préservé")


def test_frontend_echappement_html():
    """Aucun texte d'origine courtier/utilisateur ne doit être injecté en
    innerHTML sans esc() : le tableau de bord est exposé sur le Wi-Fi local
    (accès iPhone/Android documenté dans le README)."""
    import re
    racine = Path(__file__).resolve().parents[2] / "frontend" / "js"
    # champs connus pour transporter du texte non maîtrisé
    champs = ("e.error", "e.symbol", "e.action", "e.timestamp", "p.symbol", "p.type")
    fautes = []
    for fichier in ("auto_trader.js", "mt5.js"):
        code = (racine / fichier).read_text(encoding="utf-8")
        for champ in champs:
            # `${e.error}` non échappé (on tolère `${esc(e.error ...)}`)
            for m in re.finditer(r"\$\{([^}]*)\}", code):
                expr = m.group(1)
                if champ in expr and "esc(" not in expr:
                    fautes.append(f"{fichier} : ${{{expr}}}")
    assert not fautes, "interpolation HTML non échappée :\n  " + "\n  ".join(fautes)

    # La devise et les libellés d'axe du graphique viennent du courtier et de
    # la base : ils passaient bruts en innerHTML.
    charts = (racine / "charts.js").read_text(encoding="utf-8")
    for expr in ("${unite}", "${titre}", "${points[0].label}"):
        assert expr not in charts, f"charts.js : {expr} inséré sans échappement"

    # Un symbole ne doit JAMAIS être interpolé dans un attribut onclick :
    # esc() y est le mauvais échappement (le navigateur décode &#39; en '
    # AVANT d'analyser le JavaScript, ce qui referme la chaîne).
    app = (racine / "app.js").read_text(encoding="utf-8")
    assert "onclick=\"${handler}" not in app and "onclick='${handler}" not in app, (
        "app.js : le symbole est réinjecté dans un attribut onclick — "
        "utilisez data-symbole + addEventListener")
    assert "dataset.symbole" in app, "app.js : les chips doivent porter data-symbole"
    print("  OK — frontend : textes courtier/utilisateur échappés avant innerHTML")


def test_symboles_courtier_selectionnables():
    """Les paires du courtier doivent être proposées par l'API ET atteignables
    depuis l'interface.

    Le tableau de bord ne construisait ses chips qu'à partir de crypto+actions :
    tout le forex et tous les indices étaient invisibles, donc impossibles à
    sélectionner — les ajouter côté serveur n'aurait servi à rien.
    """
    from config import SYMBOLES_FOREX, SYMBOLES_CRYPTO

    # 1. Les paires majeures du Market Watch AvaTrade sont configurées, au
    #    format Yahoo (source des cours ; resoudre_symbole traduit ensuite).
    attendues = {"EURUSD=X", "GBPUSD=X", "USDCHF=X", "USDJPY=X",
                 "AUDUSD=X", "NZDUSD=X", "USDCAD=X", "USDSEK=X"}
    manquantes = attendues - set(SYMBOLES_FOREX)
    assert not manquantes, f"paires du courtier absentes : {sorted(manquantes)}"
    # Un nom de courtier brut ici ferait échouer le téléchargement des cours.
    for s in SYMBOLES_FOREX:
        assert s.endswith("=X"), f"{s} n'est pas au format Yahoo"
    assert all(s.endswith("-USD") for s in SYMBOLES_CRYPTO), SYMBOLES_CRYPTO

    # 2. L'interface propose bien les quatre familles (contrôle statique du JS).
    js = (Path(__file__).resolve().parents[2] / "frontend" / "js" / "app.js")
    code = js.read_text(encoding="utf-8")
    debut = code.index("function symbolesProposes")
    corps = code[debut:debut + 400]
    for famille in ("crypto", "forex", "actions", "indices"):
        assert f"data.{famille}" in corps, (
            f"les chips du tableau de bord ignorent « {famille} » : "
            f"ces symboles seraient inaccessibles depuis l'interface")

    # 3. Le panneau de trading automatique ne doit plus figer sa propre liste.
    at = (js.parent / "auto_trader.js").read_text(encoding="utf-8")
    assert "/api/symboles" in at, (
        "les chips de l'auto-trader sont codés en dur : les paires ajoutées "
        "à la configuration du serveur n'y apparaîtraient pas")
    print("  OK — paires du courtier configurées et sélectionnables dans l'interface")


def test_donnees_absentes_bloquent_le_reel():
    """Aucune donnée de marché = aucun ordre RÉEL.

    Le garde-fou « jamais d'ordre réel sur des prix fictifs » s'appuie sur
    `source_simulee`. Quand yfinance renvoyait des prix INVENTÉS, le drapeau
    était bien posé ; mais quand il ne renvoyait RIEN du tout (cas le pire :
    prix de référence à 0), l'orchestrateur laissait le drapeau à False et
    l'auto-trader n'avait plus rien à bloquer.
    """
    import utils.market_data as _md
    from agents.orchestrateur import ChefOrchestre

    chef = ChefOrchestre()

    # (a) aucune donnée du tout → prix 0 ET marqueur « simulé » armé
    _o = _md.FetcheurDonnees.obtenir_donnees
    _md.FetcheurDonnees.obtenir_donnees = staticmethod(lambda *a, **k: None)
    try:
        donnees = chef._preparer_donnees("BTC-USD")
    finally:
        _md.FetcheurDonnees.obtenir_donnees = _o
    assert donnees["prix_actuel"] == 0.0, donnees["prix_actuel"]
    assert donnees["indicateurs"].get("simule") is True, (
        "données de marché absentes non marquées comme simulées : un ordre "
        "RÉEL pourrait partir sur une analyse sans le moindre cours")

    # (b) la décision issue de ces données porte source_simulee=True
    decision = chef._prendre_decision("BTC-USD", [], donnees)
    assert decision["signal_final"].donnees["source_simulee"] is True, decision

    # (c) prix nul mais indicateurs « réels » : bloqué aussi
    d2 = {"prix_actuel": 0.0, "closes": [], "indicateurs": {}}
    dec2 = chef._prendre_decision("AAPL", [], d2)
    assert dec2["signal_final"].donnees["source_simulee"] is True, dec2
    print("  OK — données absentes ou prix nul : marqués simulés (pas d'ordre réel)")


def test_reglages_risque_persistes():
    """Les plafonds réglés via /api/risk/config doivent survivre au
    redémarrage : sinon la protection affichée n'est plus celle appliquée."""
    _isoler_config()
    import os as _os
    from utils.risk_guard import RiskGuard

    # Aucune variable d'environnement ne doit fausser le test
    sauve = {c: _os.environ.pop(c, None) for c in
             ("PERTE_MAX_JOURNALIERE_PCT", "MAX_POSITIONS",
              "EXPOSITION_MAX_PCT", "RISK_GUARD_ACTIF")}
    try:
        g = RiskGuard(persister=True)
        g.configurer({"perte_max_pct": 2.0, "max_positions": 3,
                      "exposition_max_pct": 20.0})
        # Nouvelle instance = redémarrage de l'application
        g2 = RiskGuard(persister=True)
        assert g2.perte_max_pct == 2.0, g2.perte_max_pct
        assert g2.max_positions == 3, g2.max_positions
        assert g2.exposition_max_pct == 20.0, g2.exposition_max_pct

        # Le réglage de l'INTERFACE l'emporte sur la variable d'environnement.
        # L'inverse s'appliquait avant, et c'était le pire des comportements :
        # le plafond réglé dans l'écran Réglages était silencieusement remplacé
        # au redémarrage par un `.env` oublié, jusqu'à bloquer tous les ordres.
        _os.environ["PERTE_MAX_JOURNALIERE_PCT"] = "7.5"
        g3 = RiskGuard(persister=True)
        assert g3.perte_max_pct == 2.0, \
            f"réglage utilisateur écrasé par l'environnement : {g3.perte_max_pct}"
        assert g3.max_positions == 3, "les autres réglages doivent rester restaurés"

        # Sans réglage utilisateur, l'environnement fournit la valeur initiale.
        from utils import app_config as _ac
        _ac.set("risk_config", {})
        g4 = RiskGuard(persister=True)
        assert g4.perte_max_pct == 7.5, g4.perte_max_pct
    finally:
        _os.environ.pop("PERTE_MAX_JOURNALIERE_PCT", None)
        for c, v in sauve.items():
            if v is not None:
                _os.environ[c] = v
    print("  OK — plafonds de sécurité persistés (l'interface prime sur le .env)")


def test_appels_mt5_serialises():
    """Tous les appels à la librairie MetaTrader5 passent par _mt5_lock.

    La connexion terminal est UNIQUE et non thread-safe : la boucle de
    trading (order_send) tourne pendant que le tableau de bord sonde les
    positions toutes les 5 s et que le pré-vol interroge les symboles.
    Contrôle statique — un appel nu ré-introduit se voit immédiatement.
    """
    import ast
    import utils.mt5_manager as _mm

    source = Path(_mm.__file__).read_text(encoding="utf-8")
    arbre = ast.parse(source)
    # Appels terminal à sérialiser (symbol_info_tick inclus via le préfixe)
    SENSIBLES = ("order_send", "positions_get", "symbols_get",
                 "account_info", "symbol_info", "symbol_select",
                 "history_deals_get")

    # `_lecture()` est le troisième moyen de sérialiser : elle prend
    # `_mt5_lock` avec un délai maximum (lecture d'affichage non bloquante).
    # On VÉRIFIE qu'elle le prend réellement — sans quoi l'ajouter à la liste
    # des constructions reconnues suffirait à contourner tout ce test.
    src_lecture = source.split("def _lecture(self)", 1)
    assert len(src_lecture) == 2, "_lecture() introuvable"
    corps_lecture = src_lecture[1].split("@contextmanager", 1)[0]
    assert "_mt5_lock.acquire" in corps_lecture, (
        "_lecture() ne prend pas _mt5_lock : elle ne sérialise rien")

    # Plages de lignes couvertes, soit par un `with self._mt5_lock` /
    # `self._lecture()`, soit par une méthode décorée @_sous_verrou (qui prend
    # le verrou pour l'appelant).
    verrouille: list = []
    # Blocs `with self._lecture() as X` : (début, fin, nom du drapeau)
    lectures: list = []

    class _Visiteur(ast.NodeVisitor):
        def visit_With(self, node):
            for item in node.items:
                texte = ast.unparse(item.context_expr)
                if "_mt5_lock" in texte or "self.verrou" in texte:
                    verrouille.append((node.lineno, node.end_lineno))
                elif "_lecture()" in texte:
                    verrouille.append((node.lineno, node.end_lineno))
                    nom = getattr(item.optional_vars, "id", None)
                    lectures.append((node.lineno, node.end_lineno, nom))
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if any(ast.unparse(d) == "_sous_verrou" for d in node.decorator_list):
                verrouille.append((node.lineno, node.end_lineno))
            self.generic_visit(node)

    _Visiteur().visit(arbre)
    assert verrouille, "aucune zone verrouillée détectée — test inopérant"

    nus = []
    for node in ast.walk(arbre):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in SENSIBLES:
            continue
        cible = getattr(node.func.value, "id", "")
        if cible != "mt5":                       # `import MetaTrader5 as mt5`
            continue
        if not any(d <= node.lineno <= f for d, f in verrouille):
            nus.append(f"ligne {node.lineno} : mt5.{node.func.attr}()")

    assert not nus, (
        "appels MetaTrader5 hors de _mt5_lock (course avec order_send) :\n  "
        + "\n  ".join(nus))

    # `_lecture()` peut ÉCHOUER à prendre le verrou (c'est tout son intérêt :
    # ne jamais faire attendre le tableau de bord). Un appel au terminal placé
    # dedans sans tester le drapeau parlerait donc au terminal SANS verrou —
    # exactement la course que ce test existe pour interdire.
    non_gardes = []
    for debut, fin, nom in lectures:
        assert nom, (f"ligne {debut} : `with self._lecture()` sans `as <drapeau>` — "
                     f"le résultat ne peut pas être testé")
        for node in ast.walk(arbre):
            if (not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Attribute)
                    or node.func.attr not in SENSIBLES
                    or getattr(node.func.value, "id", "") != "mt5"):
                continue
            if not (debut <= node.lineno <= fin):
                continue
            # le drapeau doit être consulté AVANT l'appel, dans le même bloc
            garde = any(
                isinstance(n, ast.Name) and n.id == nom
                and debut < n.lineno <= node.lineno
                for n in ast.walk(arbre))
            if not garde:
                non_gardes.append(f"ligne {node.lineno} : mt5.{node.func.attr}() "
                                  f"dans _lecture() sans vérifier « {nom} »")
    assert not non_gardes, (
        "appels au terminal exécutés même quand _lecture() n'a PAS obtenu le "
        "verrou :\n  " + "\n  ".join(non_gardes))
    print("  OK — appels MetaTrader5 tous sérialisés (verrou tenu ou lecture gardée)")


def test_frontend_pas_de_onclick_interpole():
    """Aucun identifiant dynamique interpolé dans un attribut onclick.

    esc() y est le MAUVAIS échappement : le navigateur décode l'entité HTML
    (&#39; → ') AVANT d'analyser le JavaScript — l'apostrophe referme la
    chaîne et le reste s'exécute comme du code (cf. renderChips dans app.js).
    """
    import re
    racine = Path(__file__).resolve().parents[2] / "frontend" / "js"
    fautes = []
    for fichier in sorted(racine.glob("*.js")):
        for m in re.finditer(r'onclick="[^"]*"', fichier.read_text(encoding="utf-8")):
            if "${" in m.group(0):
                fautes.append(f"{fichier.name} : {m.group(0)[:90]}")
    assert not fautes, (
        "handler onclick construit par interpolation — utilisez data-* + "
        "addEventListener :\n  " + "\n  ".join(fautes))
    print("  OK — aucun handler onclick construit par interpolation de chaîne")


def test_sl_tp_jamais_negatif():
    """Aucun stop-loss / take-profit négatif ne doit atteindre le courtier.

    Plusieurs agents calculent leurs niveaux par soustraction :
        tp = prix - (haut_zone - prix) * 1.5   (breakout baissier)
        tp = prix - (sl - prix) * 2.5          (momentum baissier)
        sl = prix * (1 - atr / prix * 1.5)     (mean reversion)
        tp = prediction                        (extrapolation ML)
    Sur un actif qui décroche fortement, ces formules passent SOUS ZÉRO.
    Le contrôle « du bon côté du prix » est satisfait par un nombre négatif
    pour une VENTE : rien ne l'arrêtait avant le courtier.
    """
    from models.signal import Signal, ActionSignal
    from agents.assistant import AssistantIA
    from utils.auto_trader import completer_sl_tp

    # 1. Rempart de l'assistant (vaut pour les 46 agents)
    ast = AssistantIA("TEST-000", "Agent de test", "strategies")
    sig = Signal(agent_id="TEST-000", agent_nom="Agent de test", symbole="BTC-USD",
                 action=ActionSignal.VENTE, confiance=80.0,
                 prix_entree=100.0, stop_loss=140.0, take_profit=-50.0)
    verifie = ast.verifier_signal(sig, {"closes": [100.0] * 60})
    assert (verifie.take_profit or 0) > 0 or verifie.take_profit is None, verifie.take_profit
    assert verifie.take_profit is None, "take-profit négatif conservé"

    sig2 = Signal(agent_id="TEST-000", agent_nom="Agent de test", symbole="BTC-USD",
                  action=ActionSignal.ACHAT, confiance=80.0,
                  prix_entree=100.0, stop_loss=-20.0, take_profit=130.0)
    v2 = ast.verifier_signal(sig2, {"closes": [100.0] * 60})
    assert v2.stop_loss is None, "stop-loss négatif conservé"

    # 2. Le filet SL/TP par défaut les remplace par des niveaux valides
    sl, tp = completer_sl_tp("SELL", 100.0, 140.0, -50.0)
    assert tp > 0 and sl > 0, (sl, tp)
    assert tp < 100.0 < sl, "SL/TP du mauvais côté pour une VENTE"
    sl2, tp2 = completer_sl_tp("BUY", 100.0, -20.0, 130.0)
    assert sl2 > 0 and sl2 < 100.0 < tp2, (sl2, tp2)

    # 3. Balayage sur des marchés simulés BIEN FORMÉS (low ≤ close ≤ high),
    #    régimes calme / agité / krach. Sans le rempart, ~2 % des signaux de
    #    ces agents sortent avec un SL ou un TP négatif.
    import random
    from agents.strategies.agent_breakout import AgentBreakout
    from agents.analyse_marche.agent_momentum import AgentMomentum
    from agents.data_intelligence.agent_ml_predictor import AgentMLPredicteur
    from agents.strategies.agent_mean_reversion import AgentMeanReversion

    agents = [AgentBreakout(), AgentMomentum(), AgentMLPredicteur(), AgentMeanReversion()]
    rng = random.Random(1)
    bruts_negatifs = 0
    for _ in range(400):
        closes = [100.0]
        sigma = rng.choice([0.005, 0.03, 0.10])
        for _ in range(59):
            closes.append(max(1.0, closes[-1] * (1 + rng.gauss(0, sigma))))
        donnees = {
            "closes": closes,
            "highs": [c * (1 + abs(rng.gauss(0, 0.01))) for c in closes],
            "lows":  [c * (1 - abs(rng.gauss(0, 0.01))) for c in closes],
            "volumes": [rng.uniform(1e6, 3e6) for _ in closes],
            "opens": closes,
        }
        donnees["volumes"][-1] *= rng.uniform(1, 5)
        for agent in agents:
            brut = agent._analyser("BTC-USD", donnees, {})       # calcul de l'agent
            if ((brut.stop_loss is not None and brut.stop_loss <= 0)
                    or (brut.take_profit is not None and brut.take_profit <= 0)):
                bruts_negatifs += 1
            final = agent.analyser("BTC-USD", donnees)           # + rempart assistant
            assert final.stop_loss is None or final.stop_loss > 0, \
                f"{agent.nom} : stop-loss {final.stop_loss} ≤ 0 transmis"
            assert final.take_profit is None or final.take_profit > 0, \
                f"{agent.nom} : take-profit {final.take_profit} ≤ 0 transmis"
    assert bruts_negatifs > 0, (
        "le balayage ne produit plus aucun niveau négatif en amont : le test "
        "ne prouve plus rien, revoir le jeu de données")
    print(f"  OK — SL/TP négatifs interceptés ({bruts_negatifs} cas bruts couverts)")


def test_breakout_emet_des_signaux():
    """L'agent Breakout doit pouvoir émettre des BUY/SELL.

    Sa zone de consolidation incluait la bougie courante. Or, par définition
    OHLC, low[-1] ≤ close[-1] ≤ high[-1] : « prix > max(highs) × 1.002 » et
    « prix < min(lows) × 0.998 » étaient donc mathématiquement impossibles.
    L'agent était TOTALEMENT INERTE — un quarante-cinquième des voix du
    consensus muet en permanence, sans qu'aucun test ne le signale.
    """
    import random
    from agents.strategies.agent_breakout import AgentBreakout
    from models.signal import ActionSignal

    agent = AgentBreakout()
    rng = random.Random(7)
    emis = {"BUY": 0, "SELL": 0}
    for _ in range(600):
        closes = [100.0]
        for _ in range(59):
            closes.append(max(1.0, closes[-1] * (1 + rng.gauss(0, 0.03))))
        # OHLC bien formé : le haut est au-dessus de la clôture, le bas dessous
        donnees = {
            "closes": closes,
            "highs": [c * (1 + abs(rng.gauss(0, 0.01))) for c in closes],
            "lows":  [c * (1 - abs(rng.gauss(0, 0.01))) for c in closes],
            "volumes": [rng.uniform(1e6, 3e6) for _ in closes],
        }
        donnees["volumes"][-1] *= rng.uniform(1, 5)
        s = agent._analyser("BTC-USD", donnees, {})
        if s.action == ActionSignal.ACHAT:
            emis["BUY"] += 1
        elif s.action == ActionSignal.VENTE:
            emis["SELL"] += 1
    assert emis["BUY"] > 0 and emis["SELL"] > 0, (
        f"agent Breakout inerte — aucune cassure détectable : {emis}")
    print(f"  OK — agent Breakout de nouveau opérationnel ({emis})")


def test_prix_non_finis_bloquent_le_reel():
    """Une bougie NaN (trou de flux yfinance) ne doit jamais produire d'ordre.

    `nan` est VRAI au sens booléen : le garde-fou « pas d'ordre réel sur des
    données inutilisables » testait `not float(prix or 0)` et ne se
    déclenchait donc PAS. Un stop-loss et un take-profit NaN partaient au
    courtier. yfinance renvoie des NaN sur les barres manquantes (jour férié,
    instrument suspendu) : le cas est courant, pas théorique.
    """
    import math
    import pandas as pd
    from models.market_data import MarketData
    from agents.orchestrateur import _prix_exploitable
    from utils.auto_trader import completer_sl_tp

    # 1. Les bougies non finies sont écartées à la source
    closes = [100.0 + i for i in range(60)]
    closes[-1] = float("nan")
    closes[10] = float("inf")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [1e6] * 60},
                      index=pd.date_range(end="2025-01-01", periods=60, freq="h"))
    md = MarketData.depuis_dataframe("AAPL", "1h", df)
    assert len(md.bougies) == 58, f"bougies non finies conservées : {len(md.bougies)}"
    assert math.isfinite(md.prix_actuel) and md.prix_actuel > 0, md.prix_actuel
    assert all(math.isfinite(c) for c in md.closes)

    # 2. Le garde-fou reconnaît un prix inexploitable
    for mauvais in (float("nan"), float("inf"), -1, 0, None, "abc", ""):
        assert not _prix_exploitable(mauvais), f"{mauvais!r} accepté comme prix"
    assert _prix_exploitable(123.45) and _prix_exploitable("50")

    # 3. Le filet SL/TP ne produit JAMAIS de niveau non fini
    for prix in (float("nan"), float("inf"), 0, -1, None, "abc", 100.0):
        for sl in (float("nan"), None, 0, -5, 95.0):
            for tp in (float("inf"), None, 0, -5, 110.0):
                r_sl, r_tp = completer_sl_tp("BUY", prix, sl, tp)
                assert math.isfinite(r_sl) and math.isfinite(r_tp), (prix, sl, tp, r_sl, r_tp)
                assert r_sl >= 0 and r_tp >= 0, (prix, sl, tp, r_sl, r_tp)
    print("  OK — prix NaN/infinis écartés, SL/TP toujours finis")


def test_api_entrees_malformees():
    """Aucun endpoint ne doit répondre 500 sur une entrée malformée.

    Un 500 masque l'exception derrière « Erreur interne du serveur » :
    l'utilisateur ne sait pas ce qui ne va pas, et sur /api/risk/config les
    plafonds étaient appliqués À MOITIÉ avant l'exception — la protection
    réelle ne correspondait plus à celle affichée.
    """
    import os as _os
    _os.environ["APP_AUTH"] = "off"
    from fastapi.testclient import TestClient
    from backend.main import app, normaliser_symboles

    # Client rattaché à un compte abonné : sans session, l'application
    # répond 401 partout (cf. python/tests/_client.py).
    import _client
    client = _client.client(app)
    valeurs = [None, "", "abc", 0, -1, [], {}, [1, 2], {"a": 1}, True, "NaN", " "]
    cibles = {
        "/api/risk/config": ["actif", "perte_max_pct", "max_positions", "exposition_max_pct"],
        "/api/notifications/config": ["webhook_url", "ntfy_topic"],
        "/api/analyser": ["symboles"],
        "/api/preflight": ["symboles"],
        "/api/auto-trader/start": ["symboles", "interval"],
    }
    for url, champs in cibles.items():
        for corps in (None, [], "texte", 42):
            r = client.post(url, json=corps)
            assert r.status_code < 500, f"{url} <- {corps!r} : {r.status_code}"
        for champ in champs:
            for v in valeurs:
                r = client.post(url, json={champ: v})
                assert r.status_code < 500, f"{url} <- {{{champ}: {v!r}}} : {r.status_code}"

    # Les plafonds de sécurité : tout ou rien, jamais à moitié appliqués
    from utils.risk_guard import get_risk_guard
    g = get_risk_guard()
    avant = (g.perte_max_pct, g.max_positions, g.exposition_max_pct)
    r = client.post("/api/risk/config",
                    json={"perte_max_pct": 3.0, "max_positions": "abc"})
    assert r.status_code == 200 and r.json().get("success") is False, r.json()
    apres = (g.perte_max_pct, g.max_positions, g.exposition_max_pct)
    assert avant == apres, (
        f"réglage partiellement appliqué malgré l'erreur : {avant} -> {apres}")

    # Une chaîne vaut UN symbole, jamais une suite de caractères
    assert normaliser_symboles("BAC") == ["BAC"], (
        "« BAC » découpé en B, A, C — trois VRAIS tickers analysés à tort")
    assert normaliser_symboles(42) == [] and normaliser_symboles(None) == []
    assert normaliser_symboles([1, 2, "AAPL"]) == ["AAPL"]
    print("  OK — API : entrées malformées refusées proprement (aucun 500)")


def test_saisie_au_format_courtier():
    """Le nom AFFICHÉ par l'interface doit être accepté en saisie.

    Le tableau de bord montre les noms du Market Watch MetaTrader (BTCUSD,
    AUDUSD) parce que ce sont ceux du courtier. L'utilisateur les recopie donc
    dans les champs de saisie — mais les cours se téléchargent au format Yahoo
    (BTC-USD, AUDUSD=X). Sans traduction, yfinance ne trouvait rien et
    l'application basculait sur des prix INVENTÉS : Bitcoin à 89, AUDUSD à
    128, avec volume et perte au stop calculés dessus.
    """
    from config import vers_symbole_yahoo as v
    from backend.main import normaliser_symboles

    attendu = {
        "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "XRPUSD": "XRP-USD",
        "AUDUSD": "AUDUSD=X", "EURUSD": "EURUSD=X", "USDSEK": "USDSEK=X",
        "GBPJPY": "GBPJPY=X",          # paire hors listes → heuristique devises
        "GSPC": "^GSPC",
        "AAPL": "AAPL", "MSFT": "MSFT",   # actions : identique
        # déjà au format Yahoo → inchangé
        "BTC-USD": "BTC-USD", "AUDUSD=X": "AUDUSD=X", "^GSPC": "^GSPC",
        "": "",
    }
    for saisie, cible in attendu.items():
        assert v(saisie) == cible, f"{saisie!r} → {v(saisie)!r} (attendu {cible!r})"

    # Un symbole inconnu n'est PAS déformé (sinon on interrogerait un autre
    # instrument que celui demandé)
    assert v("US30") == "US30" and v("XYZ") == "XYZ"

    # La traduction s'applique bien au point d'entrée de l'API
    assert normaliser_symboles("BTCUSD") == ["BTC-USD"]
    assert normaliser_symboles(["AUDUSD", "GSPC"]) == ["AUDUSD=X", "^GSPC"]
    print("  OK — saisie au format courtier traduite (plus de prix inventés)")


def test_matieres_premieres_tradables():
    """L'or (GOLD.TR) et le pétrole (CrudeOIL) du Market Watch doivent être
    tradables de bout en bout.

    Ces deux instruments n'ont AUCUN rapport lexical avec leur ticker de cours
    (GC=F, CL=F) : sans table de traduction explicite, aucun des maillons ne
    fonctionne. L'interface afficherait « GC=F » — introuvable dans le Market
    Watch —, la saisie « GOLD.TR » partirait chez yfinance qui ne connaît pas
    ce nom (donc des prix INVENTÉS), et la résolution courtier réduirait
    « CL=F » à sa racine « CL »… c'est-à-dire Colgate-Palmolive.
    """
    from config import (SYMBOLES_MATIERES, NOMS_AFFICHES, ALIAS_COURTIER,
                        vers_symbole_yahoo as v, _nom_courtier)
    from backend.main import normaliser_symboles

    # 1. Les deux instruments sont configurés, au format Yahoo (source des
    #    cours) — un nom de courtier ici ferait échouer le téléchargement.
    assert set(SYMBOLES_MATIERES) == {"GC=F", "CL=F"}, SYMBOLES_MATIERES

    # 2. L'interface les AFFICHE sous le nom du Market Watch.
    assert _nom_courtier("GC=F") == "GOLD.TR"
    assert _nom_courtier("CL=F") == "CrudeOIL"

    # 3. …et ce nom-là, recopié dans un champ de saisie, revient au bon ticker
    #    (avec ses variantes courantes chez les courtiers).
    for saisie, cible in {
        "GOLD.TR": "GC=F", "gold.tr": "GC=F", "GOLDTR": "GC=F",
        "GOLD": "GC=F", "XAUUSD": "GC=F",
        "CrudeOIL": "CL=F", "CRUDEOIL": "CL=F", "WTI": "CL=F",
        "USOIL": "CL=F",
        "GC=F": "GC=F", "CL=F": "CL=F",         # déjà Yahoo → inchangé
    }.items():
        assert v(saisie) == cible, f"{saisie!r} → {v(saisie)!r} (attendu {cible!r})"
    assert normaliser_symboles(["GOLD.TR", "CrudeOIL"]) == ["GC=F", "CL=F"]

    # 4. La résolution chez le courtier n'utilise QUE les noms déclarés : une
    #    racine générique (« GC », « CL ») viserait un autre instrument.
    for ticker, alias in ALIAS_COURTIER.items():
        assert alias, f"{ticker} sans nom courtier"
        racine = ticker.split("=")[0]
        assert racine not in alias, (
            f"« {racine} » comme nom courtier de {ticker} : un ordre RÉEL "
            f"pourrait partir sur un tout autre instrument")
    assert "GOLD.TR" in ALIAS_COURTIER["GC=F"]
    assert "CrudeOIL" in ALIAS_COURTIER["CL=F"]

    # 5. L'API les expose, et l'interface construit bien une chip pour eux.
    from backend.main import app
    # getattr : `app.routes` ne contient pas que des routes déclarées ici —
    # il y a les montages de fichiers statiques, et depuis FastAPI 0.141 les
    # routeurs inclus (licence/abonnement) y figurent comme un objet différé
    # SANS attribut `path`. Un `r.path` nu faisait échouer ce test sur une
    # simple montée de version, pour une raison sans rapport avec les
    # matières premières qu'il vérifie.
    routes = {getattr(r, "path", "") for r in app.routes}
    assert "/api/symboles" in routes
    src = (Path(__file__).resolve().parents[2] / "backend" / "main.py").read_text(encoding="utf-8")
    assert '"matieres": SYMBOLES_MATIERES' in src, (
        "/api/symboles n'expose pas les matières premières : les chips du "
        "tableau de bord resteraient vides pour l'or et le pétrole")

    js = (Path(__file__).resolve().parents[2] / "frontend" / "js" / "app.js")
    code = js.read_text(encoding="utf-8")
    debut = code.index("function symbolesProposes")
    assert "data.matieres" in code[debut:debut + 400], (
        "les chips du tableau de bord ignorent « matieres » : GOLD.TR et "
        "CrudeOIL seraient inaccessibles depuis l'interface")
    # Miroir JS des noms affichés : sans lui, la chip montrerait « GC=F ».
    for ticker, nom in NOMS_AFFICHES.items():
        assert f"'{ticker}': '{nom}'" in code, (
            f"labelSymbole n'affiche pas {ticker} en « {nom} » — "
            f"introuvable dans le Market Watch du courtier")
    print("  OK — GOLD.TR et CrudeOIL tradables (affichage, saisie, courtier)")


def test_compte_demo_pas_annonce_reel():
    """Un compte de démonstration ne doit pas être annoncé comme RÉEL.

    `est_reel()` signifie « les ordres partent vraiment au courtier », ce qui
    est vrai d'un compte démo. Le pré-vol l'affichait pourtant en « Compte
    RÉEL connecté — chaque signal engagera de l'argent réel » sur un serveur
    Ava-Demo : un avertissement faux, qui décrédibilise le vrai.
    """
    from utils.mt5_manager import MT5Manager

    m = MT5Manager()
    m._lib_available = True
    m._connected = True

    # Compte démo déclaré par le courtier (ACCOUNT_TRADE_MODE = 0)
    m._account_info = {"account": "101717960", "server": "Ava-Demo 1-MT5",
                       "simulated": False, "demo": True, "account_type": "demo"}
    assert m.est_reel() is True, "les ordres partent bien au courtier"
    assert m.est_demo() is True, "compte démo non reconnu"

    # Compte financé
    m._account_info = {"account": "5", "server": "Ava-Real 1-MT5",
                       "simulated": False, "demo": False, "account_type": "real"}
    assert m.est_reel() is True and m.est_demo() is False

    # Repli sur le nom du serveur quand le courtier ne déclare rien
    m._account_info = {"account": "7", "server": "Ava-Demo 1-MT5",
                       "simulated": False, "account_type": "real"}
    assert m.est_demo() is True, "serveur « Demo » ignoré"

    # Hors connexion : ni réel ni démo
    m._connected = False
    assert m.est_reel() is False and m.est_demo() is False

    # Le pré-vol doit refléter la distinction
    from utils import preflight

    class _Demo:
        _lib_available = True
        def est_reel(self): return True
        def est_demo(self): return True
        def equity_live(self): return 9999.86
        def get_status(self):
            return {"connected": True,
                    "account_info": {"account": "101717960",
                                     "server": "Ava-Demo 1-MT5", "currency": "EUR"}}
    checks = preflight._verifier_compte(_Demo())
    titres = [c["titre"] for c in checks]
    assert any("DÉMO" in t for t in titres), titres
    # L'avertissement « argent réel engagé » ne doit PAS apparaître sur un démo
    assert not any("seront RÉELS" in t for t in titres), titres
    assert not any("engagera de l'argent réel" in c.get("detail", "")
                   for c in checks), checks
    # …et le caractère fictif doit être dit explicitement
    assert any("fictif" in c.get("detail", "") or "sans argent réel" in c["titre"]
               for c in checks), checks
    print("  OK — compte démo annoncé comme démo (pas « argent réel »)")


def test_env_illisible_ne_casse_pas_le_demarrage():
    """AUDIT : une valeur numérique inexploitable dans le .env faisait
    ÉCHOUER LE DÉMARRAGE COMPLET de l'application.

    `config.py` et `RiskGuard.__init__` appelaient `float(os.getenv(...))` nu.
    Sur « CAPITAL_INITIAL=100 000 » ou « PERTE_MAX_JOURNALIERE_PCT=5,0 » —
    deux fautes de frappe naturelles, dont l'une avec la virgule décimale
    française — l'import de `config` levait ValueError, et le singleton
    RiskGuard devenait impossible à construire : TOUTES les routes (statut,
    positions, ordres, pré-vol) répondaient « Erreur interne », sans que rien
    n'indique le vrai coupable. Un réglage illisible doit retomber sur son
    défaut, jamais empêcher l'application de tourner.
    """
    import importlib
    import config as _config
    from utils import risk_guard

    anciens = {c: os.environ.get(c) for c in
               ("CAPITAL_INITIAL", "LEVIER_MAX", "MAX_POSITIONS", "BACKEND_PORT",
                "PERTE_MAX_JOURNALIERE_PCT", "EXPOSITION_MAX_PCT")}
    try:
        os.environ["CAPITAL_INITIAL"] = "100 000"      # espace de milliers
        os.environ["LEVIER_MAX"] = "abc"               # pas un nombre
        os.environ["MAX_POSITIONS"] = ""               # vide
        os.environ["BACKEND_PORT"] = "huit-mille"
        cfg = importlib.reload(_config)
        assert cfg.CAPITAL_INITIAL == 100000.0, cfg.CAPITAL_INITIAL
        assert cfg.LEVIER_MAX == 10.0, cfg.LEVIER_MAX      # défaut appliqué
        assert cfg.MAX_POSITIONS_SIMULTANÉES == 50
        assert cfg.BACKEND_PORT == 8000

        os.environ["PERTE_MAX_JOURNALIERE_PCT"] = "5,0"    # virgule décimale
        os.environ["EXPOSITION_MAX_PCT"] = "n/a"
        os.environ["MAX_POSITIONS"] = "12"
        rg = importlib.reload(risk_guard)
        g = rg.RiskGuard()
        assert g.perte_max_pct == 5.0, g.perte_max_pct     # virgule comprise
        assert g.exposition_max_pct == 1000.0              # défaut appliqué
        assert g.max_positions == 12
        # et il reste utilisable : evaluer() ne doit pas lever
        autorise, _ = g.evaluer({"equity": 1000.0, "nb_positions": 0,
                                 "exposition_pct": 0.0})
        assert autorise
    finally:
        for cle, val in anciens.items():
            if val is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = val
        importlib.reload(_config)
        importlib.reload(risk_guard)
    print("  OK — .env illisible : défaut appliqué, démarrage préservé")


def test_champs_ordre_non_numeriques():
    """AUDIT : un champ numérique non exploitable dans un ordre faisait
    remonter une exception EN PLEIN ENVOI.

    `/api/mt5/trade` accepte un JSON libre, et la décision d'un agent peut
    porter un niveau NaN. `float(signal.get("stop_loss") or 0)` et
    `float(levier)` (dans volume_cible) levaient alors ValueError/TypeError
    depuis `_send_order_real`, hors de tout `except` : l'ordre n'était NI
    envoyé NI journalisé, et l'appelant ne lisait qu'un « Erreur interne »
    sans rapport avec la cause. Ces champs doivent suivre la même règle que
    le volume et le prix : inexploitable = ABSENT.
    """
    import sys
    from utils import trading_config

    # volume_cible ne doit jamais lever sur un levier inexploitable
    trading_config.forcer_en_memoire(levier_max=10.0, levier=4.0, levier_auto=False)
    attendu = trading_config.volume_cible(1000, 50, 1, levier=4.0)
    for mauvais in ("abc", [], {"a": 1}, "", None):
        v = trading_config.volume_cible(1000, 50, 1, levier=mauvais)
        assert v is not None and v > 0, mauvais
    assert trading_config.volume_cible(1000, 50, 1, levier="abc") == attendu

    # chemin d'ordre complet avec un faux courtier
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_mt5_execution import _faux_mt5
    faux, _etat = _faux_mt5()
    ancien = sys.modules.get("MetaTrader5")
    sys.modules["MetaTrader5"] = faux
    try:
        from utils.mt5_manager import MT5Manager
        m = MT5Manager()
        m._lib_available = True
        m._connected = True
        m._account_info = {"equity": 10000.0, "balance": 10000.0, "currency": "USD",
                           "simulated": False, "demo": False, "account_type": "real",
                           "server": "Ava-Real 1-MT5", "account": "12345"}
        for champ, valeur in (("levier", "abc"), ("levier", []), ("levier", {"a": 1}),
                              ("stop_loss", "abc"), ("stop_loss", float("nan")),
                              ("take_profit", ["x"]), ("confiance", "abc")):
            r = m.execute_trade({"symbol": "BTC-USD", "action": "BUY",
                                 "volume": 0.1, champ: valeur})
            assert r.get("success"), f"{champ}={valeur!r} → {r.get('error')}"
        # et le stop réellement envoyé reste un nombre fini et positif
        envoye = _etat["ordres"][-1]
        assert isinstance(envoye["sl"], float) and envoye["sl"] > 0, envoye["sl"]
        assert isinstance(envoye["tp"], float) and envoye["tp"] > 0, envoye["tp"]
    finally:
        if ancien is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = ancien
    print("  OK — ordre : SL/TP/levier illisibles traités comme absents (aucune exception)")


def test_agents_survivent_aux_cours_absents():
    """AUDIT : six agents plantaient quand les cours étaient indisponibles.

    Quand le téléchargement échoue, l'orchestrateur transmet `"closes": []`
    (cf. `_preparer_donnees`). Or six agents lisaient
    `donnees.get("closes", [None])[-1]` : la valeur par défaut ne sert QUE si
    la clé est absente, donc sur une liste vide l'indexation levait
    IndexError. Ces agents retombaient en signal neutre « Erreur: list index
    out of range » — six voix perdues sur quarante-cinq, au moment précis où
    le consensus compte le plus. Aucun agent ne doit sortir en erreur sur des
    données dégradées.
    """
    from agents import TOUS_LES_AGENTS
    from models.signal import ActionSignal

    vide = {"closes": [], "highs": [], "lows": [], "volumes": [], "opens": [],
            "prix_actuel": 0.0, "variation_24h": 0.0, "indicateurs": {"simule": True},
            "portfolio": {}, "historique_valeur": [], "trades": [], "info": {}}
    cas = {
        "cours absents": vide,
        "une seule bougie": {**vide, "closes": [100.0], "highs": [101.0],
                             "lows": [99.0], "volumes": [1.0], "opens": [100.0],
                             "prix_actuel": 100.0},
        "clés manquantes": {"prix_actuel": 10.0},
    }
    for nom, donnees in cas.items():
        for agent in TOUS_LES_AGENTS:
            s = agent.analyser("TEST", dict(donnees))
            assert not s.raisonnement.startswith("Erreur"), \
                f"[{nom}] {agent.id} {agent.nom} : {s.raisonnement[:120]}"
            # et rien d'inexploitable ne doit ressortir vers le courtier
            if s.action in (ActionSignal.ACHAT, ActionSignal.VENTE):
                for champ in ("prix_entree", "stop_loss", "take_profit"):
                    v = getattr(s, champ)
                    assert v is None or (v == v and v > 0), \
                        f"[{nom}] {agent.id} : {champ}={v}"
    print("  OK — 45 agents : aucun ne plante quand les cours sont indisponibles")


def test_heures_marche_datetime_naif():
    """AUDIT : un datetime SANS fuseau faisait lever marche_ouvert().

    `_us_dst()` compare l'instant à des bornes tz-aware ; un naïf — ce que
    renvoie `datetime.utcnow()`, l'appel qu'on écrit spontanément — donnait
    « can't compare offset-naive and offset-aware datetimes ». L'exception
    remontait dans le cycle de l'auto-trader et interrompait l'analyse des
    symboles suivants. Un naïf est désormais lu comme de l'UTC, donc
    strictement équivalent à son homologue horodaté.
    """
    from datetime import datetime as _dtm, timezone as _tz
    from utils.market_hours import marche_ouvert

    for naif in (_dtm(2026, 7, 14, 15, 0),      # mardi, séance US (été)
                 _dtm(2026, 1, 14, 15, 0),      # mercredi, séance US (hiver)
                 _dtm(2026, 7, 18, 12, 0)):     # samedi
        aware = naif.replace(tzinfo=_tz.utc)
        for symbole in ("AAPL", "EURUSD=X", "GC=F", "BTC-USD"):
            assert marche_ouvert(symbole, naif) == marche_ouvert(symbole, aware), \
                f"{symbole} @ {naif} : naïf et UTC doivent donner le même verdict"
    print("  OK — heures de marché : datetime naïf lu comme UTC (plus d'exception)")


def test_tableau_de_bord_ne_gele_pas_pendant_une_connexion():
    """PANNE SIGNALÉE : « quand il réussit, ça me met la fenêtre ne répond pas ».

    Le tableau de bord sonde le terminal en continu (statut 3 s, portefeuille
    et positions 5 s). Ces lectures prenaient `_mt5_lock` de façon BLOQUANTE.
    Pendant une connexion — où `mt5.initialize()` met 30 à 60 s le temps de
    lancer le terminal — chaque sondage restait bloqué pour toute la durée :
    les requêtes s'empilaient dans le pool de threads du serveur, l'épuisaient,
    et plus rien ne répondait, y compris des écrans sans rapport (détection
    d'Ollama). Une lecture d'affichage ne doit JAMAIS attendre une opération
    longue : elle rend la dernière valeur connue et signale `occupe`.
    """
    import sys
    import threading
    import time as _t

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_mt5_execution import _faux_mt5
    faux, _ = _faux_mt5()
    faux.positions_get = lambda **k: []
    faux.history_deals_get = lambda *a, **k: []

    class _TI:
        trade_allowed = True
    faux.terminal_info = lambda: _TI()
    ancien = sys.modules.get("MetaTrader5")
    sys.modules["MetaTrader5"] = faux
    try:
        from utils.mt5_manager import MT5Manager
        m = MT5Manager()
        m._lib_available = True
        m._connected = True
        m._account_info = {"account": "1", "server": "S", "equity": 10.0,
                           "balance": 10.0, "margin": 0.0, "free_margin": 10.0,
                           "currency": "USD", "leverage": 30, "demo": True,
                           "account_type": "demo", "simulated": False}

        # Un thread monopolise le terminal, comme le fait une connexion lente.
        relache = threading.Event()

        def occuper():
            with m._mt5_lock:
                relache.wait(10)
        th = threading.Thread(target=occuper, daemon=True)
        th.start()
        _t.sleep(0.2)
        try:
            # Chaque lecture doit rendre la main sans attendre la fin.
            for nom, appel in (("get_status", m.get_status),
                               ("get_positions", m.get_positions),
                               ("get_portfolio", m.get_portfolio),
                               ("algotrading_actif", m.algotrading_actif),
                               ("get_historique_reel", m.get_historique_reel)):
                t0 = _t.monotonic()
                appel()
                duree = _t.monotonic() - t0
                assert duree < 3.0, (
                    f"{nom} a attendu {duree:.1f}s alors que le terminal était "
                    f"occupé — le tableau de bord gèle")
            # et l'état est signalé comme possiblement périmé
            assert m.get_status().get("occupe") is True
        finally:
            relache.set()
            th.join(timeout=5)

        # Terminal de nouveau libre : les lectures repartent normalement une
        # fois le coupe-circuit expiré (il évite de re-payer l'attente à
        # chaque lecture tant que le terminal est connu occupé).
        from utils.mt5_manager import BACKOFF_OCCUPE_S
        _t.sleep(BACKOFF_OCCUPE_S + 0.3)
        assert m.get_status().get("occupe") is False, (
            "le terminal est libre : les lectures doivent redevenir directes")
    finally:
        if ancien is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = ancien
    print("  OK — lectures non bloquantes : plus de gel pendant une connexion")


def test_echec_de_connexion_ne_laisse_pas_un_etat_fantome():
    """PANNE SIGNALÉE : « le projet ne veut plus se connecter à mon compte ».

    Sur un login refusé, le code appelait `mt5.shutdown()` — donc fermait le
    lien terminal — mais laissait `_connected` et `_account_info` à leurs
    valeurs de la session PRÉCÉDENTE. Le tableau de bord annonçait alors
    « Connecté » sur un lien mort, les lectures rendaient des chiffres périmés,
    et plus rien ne semblait fonctionner jusqu'au redémarrage. Un échec doit
    laisser un état franchement déconnecté, et une nouvelle tentative doit
    pouvoir aboutir immédiatement.
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_mt5_execution import _faux_mt5
    faux, _ = _faux_mt5()
    etat = {"ok": False}
    faux.initialize = lambda **kw: (etat["ok"] if "login" in kw else True)
    faux.login = lambda *a, **k: etat["ok"]
    faux.shutdown = lambda: None
    faux.positions_get = lambda **k: []

    class _AI:
        login = 777; name = "T"; server = "Ava-Real 9-MT5"; balance = 500.0
        equity = 500.0; margin = 0.0; margin_free = 500.0; currency = "EUR"
        leverage = 30; trade_mode = 2
    faux.account_info = lambda: _AI() if etat["ok"] else None
    ancien = sys.modules.get("MetaTrader5")
    sys.modules["MetaTrader5"] = faux
    try:
        from utils.mt5_manager import MT5Manager
        m = MT5Manager()
        m._lib_available = True
        m.find_path = lambda: None
        # session déjà ouverte (reconnexion automatique au démarrage)
        m._connected = True
        m._account_info = {"account": "111", "server": "Ava-Demo 1-MT5",
                           "equity": 1000.0, "simulated": False, "demo": True}

        r = m.connect(999888, "mauvais", "real", "Ava-Real 9-MT5")
        assert not r.get("success")
        assert m.get_status().get("connected") is False, (
            "après shutdown(), l'application doit se déclarer déconnectée")
        assert r.get("detail"), "l'erreur brute de MetaTrader doit être remontée"

        # Une tentative valide juste après doit aboutir.
        etat["ok"] = True
        r2 = m.connect(777, "bonmotdepasse", "real", "Ava-Real 9-MT5")
        assert r2.get("success"), r2.get("error")
        assert m.get_status().get("connected") is True

        # Un serveur ABSENT de la liste de référence doit être accepté :
        # AvaTrade en exploite d'autres selon l'entité et la région.
        m2 = MT5Manager()
        m2._lib_available = True
        m2.find_path = lambda: None
        assert m2.connect(777, "bonmotdepasse", "real",
                          "AvaTradeEU-Real-Autre").get("success")
    finally:
        if ancien is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = ancien
    print("  OK — connexion : échec propre, reprise immédiate, serveur libre")


def test_serveur_saisie_libre_dans_l_interface():
    """PANNE SIGNALÉE : « MetaTrader ne me propose plus les serveurs AvaTrade ».

    La fenêtre de connexion imposait un `<select>` de six noms écrits en dur :
    quiconque avait un compte sur un autre serveur AvaTrade n'avait AUCUN
    moyen de saisir le sien. Le champ doit accepter une saisie libre, les
    suggestions venant des serveurs réellement connus du terminal.
    """
    racine = Path(__file__).resolve().parents[2]
    html = (racine / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (racine / "frontend" / "js" / "mt5.js").read_text(encoding="utf-8")

    assert '<select id="mt5-server"' not in html, (
        "le serveur est de nouveau une liste FERMÉE : un compte hors liste "
        "redevient impossible à connecter")
    assert 'id="mt5-server"' in html and 'list="mt5-server-options"' in html, (
        "le champ serveur doit être une saisie libre avec suggestions")
    assert '<datalist id="mt5-server-options">' in html
    assert "/api/mt5/servers" in js, (
        "les suggestions doivent venir du serveur, pas d'une liste figée en JS")
    # Basculer démo/réel ne doit pas écraser un nom saisi à la main.
    assert "_serveursReference" in js, (
        "le basculement démo/réel doit épargner un serveur saisi manuellement")
    print("  OK — serveur AvaTrade : saisie libre + suggestions du terminal")


def test_ollama_systeme_detecte():
    """PANNE SIGNALÉE : « il ne détectait plus Ollama installé ».

    L'application ne cherchait le binaire que dans SON dossier
    (~/.agence_financiere/ollama). Un Ollama installé normalement depuis
    ollama.com — le cas le plus courant — restait invisible : l'écran de
    configuration affichait « IA locale non détectée » et proposait un
    téléchargement de ~3 Go déjà fait, sans jamais démarrer le serveur présent.
    """
    import stat
    import tempfile
    from utils.ollama_embedded import OllamaEmbedded

    tmp = Path(tempfile.mkdtemp())
    faux = tmp / "ollama"
    faux.write_bytes(b"#!/bin/sh\nexit 0\n")
    faux.chmod(faux.stat().st_mode | stat.S_IEXEC)
    path0 = os.environ.get("PATH", "")
    os.environ["PATH"] = str(tmp) + os.pathsep + path0
    try:
        o = OllamaEmbedded()
        assert not o.est_installe(), "aucun binaire embarqué dans ce test"
        assert o.ollama_systeme() is not None, (
            "un Ollama installé sur la machine doit être détecté")
        assert o.est_disponible(), "Ollama système = Ollama utilisable"
        assert o.binaire() == faux
        st = o.get_status()
        assert st["disponible"] and st["installe_systeme"]
        assert not st["installe"], "l'embarqué reste distinct du système"
    finally:
        os.environ["PATH"] = path0
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    # Sans binaire nulle part, on retombe bien sur « absent ».
    os.environ["PATH"] = ""
    try:
        assert OllamaEmbedded().ollama_systeme() is None
    finally:
        os.environ["PATH"] = path0
    print("  OK — Ollama installé sur la machine détecté (plus de re-téléchargement)")


def test_ipc_timeout_explique_la_vraie_cause():
    """PANNE SIGNALÉE (captures d'écran) : « (-10005, 'IPC timeout') ».

    Le terminal MetaTrader ne répondait pas parce qu'une boîte de dialogue y
    était restée ouverte (la fenêtre « Se connecter »). Le message d'erreur
    accusait alors le NOM DU SERVEUR — qui n'y était pour rien — et envoyait
    l'utilisateur corriger un réglage correct. Quand la communication échoue,
    la marche à suivre doit passer AVANT toute considération sur le serveur.

    Deux `initialize()` étaient par ailleurs enchaînés sans `shutdown()`
    intermédiaire : la seconde tentative repartait sur un canal déjà en échec.
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_mt5_execution import _faux_mt5
    faux, _ = _faux_mt5()
    appels = []
    faux.initialize = lambda **kw: (appels.append("initialize"), False)[1]
    faux.login = lambda *a, **k: False
    faux.last_error = lambda: (-10005, "IPC timeout")
    faux.shutdown = lambda: appels.append("shutdown")
    faux.account_info = lambda: None
    ancien = sys.modules.get("MetaTrader5")
    ancien_essais = os.environ.get("MT5_ESSAIS_IPC")
    sys.modules["MetaTrader5"] = faux
    os.environ["MT5_ESSAIS_IPC"] = "1"
    try:
        from utils.mt5_manager import MT5Manager
        m = MT5Manager()
        m._lib_available = True
        m.find_path = lambda: None
        r = m.connect(101743736, "motdepasse", "demo", "Ava-Demo 1-MT5")

        assert not r.get("success")
        msg = r.get("error", "")
        assert "Se connecter" in msg and "dialogue" in msg, (
            "la cause réelle (boîte de dialogue ouverte dans le terminal) doit "
            f"être nommée ; message obtenu : {msg}")
        assert "ne figure pas parmi ceux connus" not in msg, (
            "sur une panne de communication, le nom du serveur ne doit pas "
            "être mis en cause")
        assert "IPC timeout" in r.get("detail", ""), (
            "l'erreur brute reste disponible pour le diagnostic")
        # shutdown() intercalé entre les tentatives
        assert "shutdown" in appels, (
            "la communication doit être refermée avant de réessayer")

        # Identifiants réellement refusés : message adapté, pas celui de l'IPC.
        faux.last_error = lambda: (-6, "Authorization failed")
        r2 = m.connect(101743736, "mauvais", "demo", "MetaQuotes-Demo")
        assert "Autorisation refusée" in r2.get("error", ""), r2.get("error")
    finally:
        if ancien is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = ancien
        if ancien_essais is None:
            os.environ.pop("MT5_ESSAIS_IPC", None)
        else:
            os.environ["MT5_ESSAIS_IPC"] = ancien_essais
    print("  OK — IPC timeout : la vraie cause est nommée, pas le serveur")


def test_serveurs_detectes_sont_de_vrais_serveurs():
    """PANNE SIGNALÉE (capture) : « Détectés sur votre terminal : Chats,
    Custom, MetaQuotes-Demo, signals ».

    Trois entrées sur quatre étaient des dossiers techniques de MetaTrader, et
    le seul vrai serveur se noyait dedans. Pire, le champ restait pré-rempli
    avec « Ava-Demo 1-MT5 » écrit en dur : la connexion partait donc vers un
    serveur que le terminal ne connaît pas.
    """
    import tempfile
    from utils.mt5_manager import MT5Manager

    racine = Path(tempfile.mkdtemp())
    bases = racine / "MetaQuotes" / "Terminal" / "ABC" / "bases"
    for parasite in ("Chats", "Custom", "signals", "Default"):
        (bases / parasite).mkdir(parents=True, exist_ok=True)
    (bases / "MetaQuotes-Demo" / "symbols").mkdir(parents=True, exist_ok=True)
    (bases / "Ava-Demo 1-MT5" / "ticks").mkdir(parents=True, exist_ok=True)
    (bases / "ICMarketsSC-Live").mkdir(parents=True, exist_ok=True)
    ancien_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(racine)
    try:
        m = MT5Manager()
        m.find_path = lambda: None
        detectes = m.serveurs_terminal()
        for parasite in ("Chats", "Custom", "signals", "Default"):
            assert parasite not in detectes, (
                f"« {parasite} » est un dossier technique, pas un serveur")
        for vrai in ("MetaQuotes-Demo", "Ava-Demo 1-MT5", "ICMarketsSC-Live"):
            assert vrai in detectes, f"« {vrai} » aurait dû être détecté"
        # `serveurs_terminal` ne doit voir QUE le disque, jamais les comptes
        # mémorisés : l'interface annonce « détectés sur votre terminal ».
        assert set(detectes) == {"MetaQuotes-Demo", "Ava-Demo 1-MT5",
                                 "ICMarketsSC-Live"}, detectes
    finally:
        if ancien_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = ancien_appdata
        import shutil as _sh
        _sh.rmtree(racine, ignore_errors=True)

    # L'interface doit pré-remplir avec un serveur DÉTECTÉ, pas avec le nom
    # AvaTrade écrit en dur.
    js = (Path(__file__).resolve().parents[2] / "frontend" / "js" / "mt5.js"
          ).read_text(encoding="utf-8")
    assert "detectes[0]" in js, (
        "le champ serveur doit être pré-rempli avec un serveur réellement "
        "présent sur le terminal")
    print("  OK — serveurs détectés : dossiers techniques écartés, champ pré-rempli")


def test_serveur_dans_un_processus_separe():
    """PANNE SIGNALÉE (vidéo) : « Agence Numérique Financière (Ne répond pas) ».

    Ce titre est celui de la FENÊTRE Windows, pas de la page web : c'est le
    processus entier qui était figé. Le serveur tournait dans un simple thread
    du processus qui fait aussi vivre la fenêtre native. Or MetaTrader5 est une
    extension C : pendant une connexion au courtier elle bloque des dizaines
    de secondes sans rendre l'interpréteur, ce qui privait la boucle de
    messages de la fenêtre — Windows la déclarait « Ne répond pas », et rien
    ne permettait de reprendre la main.

    Le serveur doit donc vivre dans un PROCESSUS distinct : MetaTrader5 ne peut
    alors plus figer la fenêtre, ni l'emporter en cas de blocage dur.
    """
    racine = Path(__file__).resolve().parents[2]
    source = (racine / "app_window.py").read_text(encoding="utf-8")

    assert "DRAPEAU_SERVEUR" in source and "subprocess.Popen" in source, (
        "le serveur doit être lancé dans un processus séparé de la fenêtre")
    # Le thread ne subsiste que comme REPLI, et UNIQUEMENT dans la fonction
    # prévue pour ça : le chemin nominal doit passer par le processus enfant.
    hors_repli = "".join(
        bloc for bloc in source.split("def ")
        if not bloc.startswith("_demarrer_thread_repli"))
    assert "threading.Thread(target=start_server" not in hors_repli, (
        "le serveur ne doit démarrer dans un thread du processus fenêtre que "
        "depuis _demarrer_thread_repli (chemin de repli)")
    # Le processus enfant ne doit ouvrir aucune fenêtre.
    assert "if MODE_SERVEUR:" in source and "sys.exit(0)" in source, (
        "le processus serveur doit sortir sans créer de fenêtre")
    # Il doit mourir avec la fenêtre, sinon il garde le port.
    assert "atexit.register(_arreter)" in source, (
        "le processus serveur doit être arrêté à la fermeture de la fenêtre")
    print("  OK — serveur en processus séparé : MetaTrader ne peut plus figer la fenêtre")


def test_connexion_rend_la_main_dans_un_delai_borne():
    """PANNE SIGNALÉE (vidéo) : le bouton reste sur « Connexion en cours… ».

    Deux tentatives de deux appels chacune, à 60 s de délai, pouvaient occuper
    le bouton plusieurs MINUTES quand le terminal ne répond pas. L'utilisateur
    en concluait — à raison — que le programme était planté. La tentative doit
    tenir dans un budget borné, chaque appel étant lui-même limité au temps
    restant.
    """
    import sys
    import time as _t
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_mt5_execution import _faux_mt5
    faux, _ = _faux_mt5()

    delais_demandes = []

    def _initialize(**kw):
        delais_demandes.append(kw.get("timeout"))
        _t.sleep(0.6)                     # terminal qui traîne
        return False
    faux.initialize = _initialize
    faux.login = lambda *a, **k: False
    faux.last_error = lambda: (-10005, "IPC timeout")
    faux.shutdown = lambda: None
    faux.account_info = lambda: None

    ancien = sys.modules.get("MetaTrader5")
    anciennes_env = {c: os.environ.get(c) for c in
                     ("MT5_ESSAIS_IPC", "MT5_CONNEXION_BUDGET_S", "MT5_INIT_TIMEOUT_MS")}
    sys.modules["MetaTrader5"] = faux
    os.environ["MT5_CONNEXION_BUDGET_S"] = "3"
    os.environ["MT5_INIT_TIMEOUT_MS"] = "30000"
    try:
        from utils.mt5_manager import MT5Manager
        m = MT5Manager()
        m._lib_available = True
        # Plusieurs installations détectées : l'échelle de stratégies est
        # longue (appel nu, identifiants, puis chaque terminal). C'est
        # justement ce cumul qui pouvait immobiliser le bouton plusieurs
        # minutes — le budget doit le trancher.
        m.find_paths = lambda: [f"C:\\MT5-{i}\\terminal64.exe" for i in range(6)]
        t0 = _t.monotonic()
        r = m.connect(101743736, "motdepasse", "demo", "Ava-Demo 1-MT5")
        duree = _t.monotonic() - t0

        assert not r.get("success")
        assert duree < 10, (
            f"la connexion a duré {duree:.1f}s pour un budget de 3s — le bouton "
            f"« Connexion en cours… » resterait figé")
        assert len(delais_demandes) < 14, (
            "toutes les stratégies ont été jouées malgré le budget épuisé")
        assert "budget de connexion dépassé" in r.get("detail", ""), r.get("detail")
        # Chaque appel est borné par le temps RESTANT, jamais par le délai plein.
        assert delais_demandes, "aucun appel n'a été tenté"
        assert max(delais_demandes) <= 30000
        assert min(delais_demandes) < 30000, (
            "le délai des derniers appels doit se réduire avec le budget restant")
        # La version de la librairie fait partie du diagnostic : un terminal
        # récent avec un paquet Python ancien donne exactement cette erreur.
        assert "librairie MetaTrader5" in r.get("detail", "")
    finally:
        if ancien is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = ancien
        for cle, val in anciennes_env.items():
            if val is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = val
    print("  OK — connexion bornée dans le temps, délai par appel décroissant")


def test_isolation_du_serveur_ne_bloque_jamais_le_demarrage():
    """RÉGRESSION SIGNALÉE : écran « Le serveur n'a pas démarré ».

    L'isolation du serveur dans un processus séparé (qui empêche MetaTrader de
    figer la fenêtre) avait été introduite SANS filet : trois défauts la
    rendaient capable d'empêcher l'application de démarrer.

    1. La sortie de l'enfant n'était pas redirigée. En version compilée la
       fenêtre est bâtie sans console : ses descripteurs de sortie sont
       invalides, l'enfant en hérite et meurt dès la première écriture de
       journal d'uvicorn — sans laisser la moindre trace.
    2. Un enfant mort menait droit à l'écran d'erreur, alors que le mode
       historique (serveur dans le processus de la fenêtre) fonctionnait.
    3. Le repli écoute sur le MÊME port : sans arrêter d'abord un enfant
       vivant mais trop lent, il échouerait sur « adresse déjà utilisée ».

    L'isolation est une protection ; elle ne doit jamais coûter le démarrage.
    """
    racine = Path(__file__).resolve().parents[2]
    source = (racine / "app_window.py").read_text(encoding="utf-8")

    # 1. sortie de l'enfant capturée dans un fichier
    assert "SERVEUR_LOG" in source, "la sortie du processus serveur doit être journalisée"
    bloc = source.split("def _lancer_serveur", 1)[1].split("def ", 1)[0]
    assert '"stdout": sortie or subprocess.DEVNULL' in bloc, (
        "sans redirection, l'enfant hérite de descripteurs invalides en "
        "version compilée et meurt sans laisser de trace")
    assert '"stderr": subprocess.STDOUT' in bloc
    assert '"stdin": subprocess.DEVNULL' in bloc

    # 2. repli garanti sur les DEUX chemins d'échec (lancement, arrêt)
    assert source.count("_demarrer_thread_repli(") >= 3, (
        "le repli doit couvrir l'échec de lancement, l'arrêt de l'enfant et "
        "l'absence de réponse")

    # 3. l'enfant est arrêté avant que le repli ne prenne le port
    repli = source.split("def _demarrer_thread_repli", 1)[1].split("def ", 1)[0]
    assert "proc.terminate()" in repli and "_serveur_mort.clear()" in repli, (
        "le repli doit d'abord libérer le port tenu par l'enfant")
    assert repli.index("proc.terminate()") < repli.index("threading.Thread(target=start_server"), (
        "l'enfant doit être arrêté AVANT de démarrer le serveur de repli")

    # 4. l'écran d'échec montre la cause, pas seulement un chemin de fichier
    assert "def _ecran_erreur" in source and "_journal_serveur()" in source, (
        "l'écran d'erreur doit afficher ce que le serveur a écrit")
    print("  OK — isolation du serveur : repli garanti, échec toujours expliqué")


def test_connexion_utilise_le_terminal_deja_ouvert():
    """PANNE SIGNALÉE : « (-10005, 'IPC timeout') », librairie 5.0.6090.

    La librairie était récente et le terminal tournait, connecté au compte
    101743736 sur Ava-Demo 1-MT5 : ni l'une ni l'autre n'étaient en cause.
    Le défaut était dans l'ORDRE des tentatives — `initialize()` recevait
    TOUJOURS `path=…`, si bien que l'appel NU, le seul qui se contente de se
    brancher sur le terminal en cours, n'était jamais essayé. Quand le chemin
    trouvé n'est pas celui du terminal qui tourne (plusieurs MetaTrader
    installés, dossier non standard), la librairie tente d'en lancer un
    SECOND : le terminal refuse la double instance et la communication expire.

    Deux garanties ici : l'appel nu passe en premier, et un terminal déjà
    connecté au bon compte n'est pas re-loggé.
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_mt5_execution import _faux_mt5
    faux, _ = _faux_mt5()

    tentatives = []
    logins = []

    def _initialize(**kw):
        tentatives.append(sorted(kw))
        if "path" in kw or "login" in kw:
            return False                  # double instance / login imposé refusés
        return True                       # initialize() NU : se branche au terminal
    faux.initialize = _initialize
    faux.login = lambda *a, **k: logins.append(a) or False
    faux.last_error = lambda: (-10005, "IPC timeout")
    faux.shutdown = lambda: None

    class _AI:
        login = 101743736; name = "Jordan"; server = "Ava-Demo 1-MT5"
        balance = 10000.0; equity = 10000.0; margin = 0.0; margin_free = 10000.0
        currency = "EUR"; leverage = 400; trade_mode = 0
    faux.account_info = lambda: _AI()

    ancien = sys.modules.get("MetaTrader5")
    sys.modules["MetaTrader5"] = faux
    try:
        from utils.mt5_manager import MT5Manager
        m = MT5Manager()
        m._lib_available = True
        m.find_paths = lambda: [r"C:\Program Files\MetaTrader 5\terminal64.exe"]

        r = m.connect(101743736, "motdepasse", "demo", "Ava-Demo 1-MT5")
        assert r.get("success"), r.get("error")
        assert tentatives, "aucune tentative"
        assert "path" not in tentatives[0] and "login" not in tentatives[0], (
            "la PREMIÈRE tentative doit être initialize() sans chemin ni "
            f"identifiants ; reçue : {tentatives[0]}")
        assert not logins, (
            "le terminal est déjà sur le bon compte : rejouer login() ne peut "
            "que casser une session qui fonctionne")

        # Reprise du compte ouvert, sans aucun identifiant.
        m2 = MT5Manager()
        m2._lib_available = True
        m2.find_paths = lambda: []
        r2 = m2.connecter_terminal_courant()
        assert r2.get("success"), r2.get("error")
        assert str((r2.get("account_info") or {}).get("account")) == "101743736"
        assert m2.get_status().get("connected") is True

        # Compte DIFFÉRENT de celui ouvert : là, un login est bien nécessaire.
        logins.clear()
        m3 = MT5Manager()
        m3._lib_available = True
        m3.find_paths = lambda: []
        m3.connect(222333444, "motdepasse", "real", "Ava-Real 1-MT5")
        assert logins, "changer de compte doit passer par login()"
    finally:
        if ancien is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = ancien
    print("  OK — le terminal en cours est joignable sans mot de passe ni serveur")


def test_interface_propose_le_compte_ouvert():
    """L'interface doit offrir le chemin le plus simple EN PREMIER.

    Redemander numéro, mot de passe et nom EXACT du serveur alors que le
    terminal est déjà connecté, c'est trois occasions de se tromper pour
    aucun gain. Le compte détecté est proposé en haut de la fenêtre, et le
    formulaire manuel est pré-rempli avec ce que dit le terminal.
    """
    racine = Path(__file__).resolve().parents[2]
    html = (racine / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (racine / "frontend" / "js" / "mt5.js").read_text(encoding="utf-8")

    assert 'id="mt5-attacher-bloc"' in html, (
        "la fenêtre doit proposer le compte déjà ouvert dans MetaTrader")
    # Proposé AVANT la saisie manuelle.
    assert html.index('id="mt5-attacher-bloc"') < html.index('id="mt5-account"'), (
        "le compte détecté doit être proposé avant le formulaire manuel")
    assert "/api/mt5/attacher" in js and "/api/mt5/diagnostic" in js
    assert "mt5ChargerCompteOuvert" in js
    # Le formulaire manuel est pré-rempli depuis le terminal.
    assert "champServeur.value = compte.serveur" in js, (
        "le serveur EXACT lu dans le terminal doit pré-remplir le champ")
    print("  OK — interface : compte ouvert proposé d'abord, champs pré-remplis")


def run():
    print("═══ Fonctionnalités (push, multi-comptes, CSV, pré-vol, robustesse) ═══")
    from utils import app_config
    _path0, _cache0 = app_config._PATH, app_config._cache
    try:
        test_notifier_ntfy()
        test_ntfy_validation()
        test_webhook_https_obligatoire()
        test_purge_base_retention()
        test_env_virgule_decimale()
        test_aucun_code_demande_par_defaut()
        test_serveur_public_sans_code_lui_aussi()
        test_interface_sait_si_un_code_existe()
        test_securite_mot_de_passe()
        test_multi_comptes()
        test_kill_switch_persistant()
        test_arret_et_suspension()
        test_encodage_windows_et_suggestions()
        test_preflight()
        test_export_csv()
        test_analyse_arriere_plan_non_bloquante()
        test_indicateurs_volumes_absents()
        test_fermeture_position_simulee()
        test_bandeau_erreurs_non_inonde()
        test_frontend_echappement_html()
        test_symboles_courtier_selectionnables()
        test_donnees_absentes_bloquent_le_reel()
        test_reglages_risque_persistes()
        test_appels_mt5_serialises()
        test_frontend_pas_de_onclick_interpole()
        test_sl_tp_jamais_negatif()
        test_breakout_emet_des_signaux()
        test_prix_non_finis_bloquent_le_reel()
        test_api_entrees_malformees()
        test_saisie_au_format_courtier()
        test_matieres_premieres_tradables()
        test_compte_demo_pas_annonce_reel()
        test_env_illisible_ne_casse_pas_le_demarrage()
        test_champs_ordre_non_numeriques()
        test_agents_survivent_aux_cours_absents()
        test_heures_marche_datetime_naif()
        test_tableau_de_bord_ne_gele_pas_pendant_une_connexion()
        test_echec_de_connexion_ne_laisse_pas_un_etat_fantome()
        test_serveur_saisie_libre_dans_l_interface()
        test_ollama_systeme_detecte()
        test_ipc_timeout_explique_la_vraie_cause()
        test_serveurs_detectes_sont_de_vrais_serveurs()
        test_serveur_dans_un_processus_separe()
        test_connexion_rend_la_main_dans_un_delai_borne()
        test_isolation_du_serveur_ne_bloque_jamais_le_demarrage()
        test_connexion_utilise_le_terminal_deja_ouvert()
        test_interface_propose_le_compte_ouvert()
    finally:
        # restaurer la config d'origine pour ne pas polluer le process
        app_config._PATH, app_config._cache = _path0, _cache0


if __name__ == "__main__":
    run()
    print("✅ Fonctionnalités OK")
