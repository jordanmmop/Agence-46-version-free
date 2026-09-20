#!/usr/bin/env python3
"""Lance toute la suite de tests. Usage : python python/tests/run_tests.py"""
import _setup  # noqa: F401
import traceback

import test_risk_guard
import test_mt5_execution
import test_agents
import test_backtest
import test_trading_reel
import test_market_hours
import test_indicators
import test_features
import test_performance
import test_moteur_ia
import test_installation
import test_msix


def main():
    modules = [test_risk_guard, test_mt5_execution, test_agents,
               test_backtest, test_trading_reel, test_market_hours,
               test_indicators, test_features, test_performance,
               test_moteur_ia, test_installation, test_msix]
    echecs = 0
    for mod in modules:
        try:
            mod.run()
        except Exception:
            echecs += 1
            print(f"  ✗ ÉCHEC dans {mod.__name__}")
            traceback.print_exc()
    print()
    if echecs:
        print(f"❌ {echecs} module(s) en échec")
        raise SystemExit(1)
    print("✅ TOUS LES TESTS PASSENT")


if __name__ == "__main__":
    main()
