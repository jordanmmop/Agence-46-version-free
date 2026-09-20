#!/usr/bin/env python3
"""Point d'entrée principal de l'Agence Numérique Financière."""
import sys
import os
import json
import argparse
import logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("agence")


def cmd_analyse(args):
    from agents.orchestrateur import ChefOrchestre
    from config import SYMBOLES_DEFAULT

    symboles = args.symboles if args.symboles else SYMBOLES_DEFAULT[:3]
    print(f"\n{'='*60}")
    print("  AGENCE NUMÉRIQUE FINANCIÈRE")
    print("  Chef d'Orchestre + 45 Agents Spécialisés")
    print(f"{'='*60}\n")

    chef = ChefOrchestre()
    print(f"Analyse de {len(symboles)} symboles: {', '.join(symboles)}")
    print("Agents actifs: 45 + 1 Chef d'Orchestre = 46 total\n")

    rapport = chef.orchestrer(symboles)

    print(f"\n{'─'*60}")
    print(f"RAPPORT D'ANALYSE - Cycle #{rapport['cycle']}")
    print(f"{'─'*60}")

    for sym, decision in rapport["decisions"].items():
        action = decision["action"]
        confiance = decision["confiance"]
        raison = decision["raisonnement"][:80]
        emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚠️" if action == "ALERT" else "⚪"
        print(f"\n{emoji} {sym}: {action} (confiance: {confiance:.0f}%)")
        print(f"   {raison}")
        if decision.get("stop_loss"):
            print(f"   SL: {decision['stop_loss']:.4f} | TP: {decision.get('take_profit', 'N/A')}")

    print(f"\n{'─'*60}")
    print("STATISTIQUES GLOBALES:")
    stats = rapport.get("stats_decisions", {})
    print(f"  Achats: {len(stats.get('achats', []))}")
    print(f"  Ventes: {len(stats.get('ventes', []))}")
    print(f"  Alertes: {len(stats.get('alertes', []))}")

    portfolio = rapport.get("portfolio", {})
    print("\nPORTEFEUILLE:")
    print(f"  Valeur: {portfolio.get('valeur_totale', 0):,.0f}€")
    print(f"  P&L: {portfolio.get('pnl_total', 0):+,.2f}€ ({portfolio.get('pnl_total_pct', 0):+.2f}%)")
    print(f"  Positions: {portfolio.get('nb_positions', 0)}")

    if args.json:
        with open("rapport_analyse.json", "w", encoding="utf-8") as f:
            json.dump(rapport, f, ensure_ascii=False, indent=2, default=str)
        print("\nRapport JSON sauvegardé: rapport_analyse.json")


def cmd_agents(args):
    from agents import TOUS_LES_AGENTS, AGENTS_PAR_GROUPE
    from agents.orchestrateur import ChefOrchestre

    print(f"\n{'='*70}")
    print("  LISTE DES 46 AGENTS DE L'AGENCE")
    print(f"{'='*70}")

    chef = ChefOrchestre()
    print(f"\n🎼 [{chef.ID}] {chef.NOM}")
    print(f"   Groupe: {chef.GROUPE}")
    print(f"   {chef.DESCRIPTION}")
    print(f"\n{'─'*70}")

    groupes_noms = {
        "analyse_marche": "ANALYSE DE MARCHÉ",
        "strategies": "STRATÉGIES DE TRADING",
        "risques": "GESTION DES RISQUES",
        "execution": "EXÉCUTION & OPÉRATIONS",
        "data_intelligence": "DATA & INTELLIGENCE",
        "reporting": "REPORTING & COMMUNICATION",
    }

    for groupe_id, agents in AGENTS_PAR_GROUPE.items():
        print(f"\n📂 {groupes_noms.get(groupe_id, groupe_id)} ({len(agents)} agents)")
        print(f"{'─'*70}")
        for agent in agents:
            print(f"  [{agent.id}] {agent.nom}")
            print(f"    → {agent.description}")

    print(f"\n{'='*70}")
    assert len(TOUS_LES_AGENTS) == 45
    print(f"TOTAL: {len(TOUS_LES_AGENTS)} agents spécialisés + 1 Chef d'Orchestre = 46 agents")
    print(f"{'='*70}")


def cmd_serveur(args):
    import uvicorn
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import BACKEND_HOST, BACKEND_PORT
    print("\nDémarrage du serveur de l'Agence Numérique Financière...")
    print(f"URL: http://{BACKEND_HOST}:{BACKEND_PORT}")
    print(f"Dashboard: http://localhost:{BACKEND_PORT}/dashboard\n")
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # reload=True redémarrait le serveur à chaque écriture .py (git pull,
    # correctif appliqué) — donc EN PLEINE SESSION de trading. Activable
    # explicitement pour le développement via MAIN_RELOAD=1.
    reload = os.getenv("MAIN_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run("backend.main:app", host=BACKEND_HOST, port=BACKEND_PORT, reload=reload)


def main():
    parser = argparse.ArgumentParser(description="Agence Numérique Financière - 46 Agents de Trading")
    subparsers = parser.add_subparsers(dest="commande")

    p_analyse = subparsers.add_parser("analyser", help="Lancer une analyse multi-agents")
    p_analyse.add_argument("symboles", nargs="*", help="Symboles à analyser (ex: BTC-USD AAPL)")
    p_analyse.add_argument("--json", action="store_true", help="Sauvegarder rapport JSON")
    p_analyse.set_defaults(func=cmd_analyse)

    p_agents = subparsers.add_parser("agents", help="Lister tous les agents")
    p_agents.set_defaults(func=cmd_agents)

    p_serveur = subparsers.add_parser("serveur", help="Démarrer le serveur web")
    p_serveur.set_defaults(func=cmd_serveur)

    args = parser.parse_args()
    if not args.commande:
        parser.print_help()
        print("\n--- DÉMARRAGE RAPIDE ---")
        print("  python main.py agents          → Liste les 46 agents")
        print("  python main.py analyser        → Lance une analyse")
        print("  python main.py analyser BTC-USD AAPL → Analyse spécifique")
        print("  python main.py serveur         → Démarre le dashboard web")
    else:
        args.func(args)


if __name__ == "__main__":
    main()
