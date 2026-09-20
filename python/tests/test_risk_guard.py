"""Tests des garde-fous de sécurité (RiskGuard)."""
import _setup  # noqa: F401
from utils.risk_guard import RiskGuard


def test_kill_switch_perte_journaliere():
    g = RiskGuard()
    g.perte_max_pct = 5.0
    # équité de départ 10000
    ok, _ = g.evaluer({"equity": 10000, "nb_positions": 0, "exposition_pct": 0})
    assert ok
    # perte de 4% → encore autorisé
    ok, _ = g.evaluer({"equity": 9600, "nb_positions": 0, "exposition_pct": 0})
    assert ok
    # perte de 6% → kill-switch
    ok, raison = g.evaluer({"equity": 9400, "nb_positions": 0, "exposition_pct": 0})
    assert not ok and "Kill-switch" in raison
    # une fois déclenché, tout ordre reste bloqué même si l'équité remonte
    ok, _ = g.evaluer({"equity": 10000, "nb_positions": 0, "exposition_pct": 0})
    assert not ok
    # réarmement manuel
    g.reset_kill()
    ok, _ = g.evaluer({"equity": 10000, "nb_positions": 0, "exposition_pct": 0})
    assert ok
    print("  OK — kill-switch perte journalière + réarmement")


def test_plafond_positions():
    g = RiskGuard()
    g.max_positions = 3
    ok, _ = g.evaluer({"equity": 10000, "nb_positions": 2, "exposition_pct": 0})
    assert ok
    ok, raison = g.evaluer({"equity": 10000, "nb_positions": 3, "exposition_pct": 0})
    assert not ok and "positions" in raison.lower()
    print("  OK — plafond de positions")


def test_plafond_exposition():
    g = RiskGuard()
    g.exposition_max_pct = 50.0
    ok, _ = g.evaluer({"equity": 10000, "nb_positions": 0, "exposition_pct": 49})
    assert ok
    ok, raison = g.evaluer({"equity": 10000, "nb_positions": 0, "exposition_pct": 55})
    assert not ok and "xposition" in raison
    print("  OK — plafond d'exposition")


def test_desactivable():
    g = RiskGuard()
    g.actif = False
    # tout passe quand le garde-fou est désactivé
    ok, _ = g.evaluer({"equity": 1, "nb_positions": 999, "exposition_pct": 999})
    assert ok
    print("  OK — désactivable")


def test_anti_force_brute():
    import os
    os.environ["LOGIN_MAX_ECHECS"] = "3"
    os.environ["LOGIN_BLOCAGE_S"] = "2"
    import importlib
    from utils import auth
    importlib.reload(auth)
    auth.reinitialiser_echecs()
    assert not auth.login_bloque()[0]
    for _ in range(3):
        auth.enregistrer_echec()
    b, r = auth.login_bloque()
    assert b and r > 0
    auth.reinitialiser_echecs()
    assert not auth.login_bloque()[0]
    print("  OK — anti-force-brute : blocage après N échecs, réarmement au succès")


def run():
    print("═══ RiskGuard ═══")
    test_kill_switch_perte_journaliere()
    test_plafond_positions()
    test_plafond_exposition()
    test_desactivable()
    test_anti_force_brute()


if __name__ == "__main__":
    run()
    print("✅ RiskGuard OK")
