"""Entry point for Railway / Render — avoids shell $PORT expansion issues."""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "python"))

log.info(f"ROOT: {root}")
log.info(f"PYTHONPATH: {sys.path[:4]}")

# Charger la config sauvegardée (moteur IA local choisi)
_cfg_file = os.path.join(os.path.expanduser("~"), ".agence_financiere", "config.json")
if os.path.exists(_cfg_file):
    try:
        import json as _json
        with open(_cfg_file) as _f:
            _cfg = _json.load(_f)
        for _k in ("USE_OLLAMA", "OLLAMA_MODEL", "MOTEUR_IA", "HERMES_MODEL"):
            if _cfg.get(_k):
                os.environ.setdefault(_k, _cfg[_k])
    except Exception:
        pass

# Choix du moteur IA — UNIQUEMENT en mode console interactif.
# Sur un serveur headless (Railway/Render), input() lèverait EOFError
# et ferait crasher le démarrage.
_moteur = os.environ.get("MOTEUR_IA", "").strip().lower()
if _moteur not in ("ollama", "hermes"):
    _moteur = "ollama"
if sys.stdin and sys.stdin.isatty():
    try:
        _modele = (os.environ.get("HERMES_MODEL", "hermes-3-llama-3.2-3b")
                   if _moteur == "hermes"
                   else os.environ.get("OLLAMA_MODEL", "llama3.2"))
        print("\n" + "="*50)
        print("  Agence Numérique Financière")
        print("="*50)
        print(f"\n  Moteur IA : {_moteur} ({_modele}) — 100 % local")
        print("\n  Moteurs disponibles (tous deux gratuits, sur cette machine) :")
        print("  [1] Ollama")
        print("  [2] Hermès")
        print("  [Entrée] Garder le moteur actuel")
        choix = input("\n  Votre choix : ").strip()
        if choix == "1":
            _moteur = "ollama"
        elif choix == "2":
            _moteur = "hermes"
        os.environ["MOTEUR_IA"] = _moteur
        os.environ["USE_OLLAMA"] = "true"
        print(f"  → Moteur {_moteur} activé.")
        print()
    except (EOFError, KeyboardInterrupt):
        log.warning("Pas de console interactive — moteur IA inchangé.")
else:
    os.environ.setdefault("MOTEUR_IA", _moteur)
    os.environ.setdefault("USE_OLLAMA", "true")

import uvicorn

# ── Aucun code d'accès, ici non plus ─────────────────────────────────
# Choix explicite du projet, valable partout : ni l'application de bureau ni
# ce serveur ne demandent de code (voir python/utils/auth.py). Rien n'est
# imposé ici — le défaut du module s'applique tel quel.
#
# Ce fichier est le point d'entrée Railway / Render (voir Procfile) : l'URL
# qu'il sert est donc PUBLIQUE. L'état effectif est journalisé au démarrage,
# pour qu'il figure noir sur blanc dans les journaux de l'hébergeur plutôt que
# de devoir être déduit. `APP_AUTH=on` dans les variables d'environnement
# rétablit le code à tout moment, sans modifier une ligne de code.


def _annoncer_authentification():
    """Écrit dans le journal de démarrage si un code est demandé, ou non."""
    from utils.auth import auth_active
    if auth_active():
        log.info("Code d'acces demande (APP_AUTH=on).")
    else:
        log.info("Aucun code d'acces : l'URL de ce serveur est ouverte a qui la "
                 "connait, y compris pour les identifiants du courtier et le "
                 "passage d'ordres. APP_AUTH=on pour en demander un.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("BACKEND_PORT", "8000")))
    try:
        _annoncer_authentification()
    except Exception as e:                      # ne doit jamais empêcher le démarrage
        log.warning(f"Etat de l'authentification indeterminable : {e}")
    log.info(f"Starting on 0.0.0.0:{port}")
    print(f"\n  App disponible sur : http://localhost:{port}\n")
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        timeout_keep_alive=75,
    )
