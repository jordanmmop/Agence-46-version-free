"""Runtimes EMBARQUÉS dans l'application (Ollama, Hermès, MetaTrader 5).

Le dossier `runtime/` est rempli AVANT la compilation par
`tools/preparer_runtimes.py` (appelé par build_exe.bat), puis empaqueté dans
l'exe par PyInstaller. L'utilisateur final n'installe donc RIEN : les moteurs
et les modèles sont déjà là au premier lancement, même sans connexion.

    runtime/
      manifest.json          ce qui a réellement été embarqué (écrit au build)
      ollama/
        ollama.exe | bin/ollama
        models/              blobs + manifests déjà tirés (OLLAMA_MODELS)
      hermes/
        bin/llama-server[.exe]
        models/*.gguf
      mt5/
        terminal/terminal64.exe   MetaTrader 5 portable, prêt à l'emploi
        mt5setup.exe              installeur (repli)

Ce module ne fait que LOCALISER ces ressources. Chaque moteur décide ensuite
quoi en faire : Ollama copie ses modèles dans son dossier de travail, Hermès
lit son GGUF sur place, MetaTrader lance l'installeur embarqué.

En développement (dépôt cloné, pas d'exe), le dossier `runtime/` de la racine
du projet est utilisé s'il existe — sinon rien n'est embarqué et les moteurs
retombent sur leur téléchargement à la demande, comme avant.
"""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NOM_DOSSIER = "runtime"


def racine() -> Optional[Path]:
    """Dossier des runtimes embarqués, ou None s'il n'y en a pas.

    Ordre : bundle PyInstaller, dossier de l'exe, racine du dépôt.

    `AGENCE_RUNTIME_DIR` court-circuite TOUT et fait autorité, y compris quand
    le dossier désigné n'existe pas : la variable est renseignée par quelqu'un
    qui sait ce qu'il veut (installation personnalisée, test d'un cas « sans
    runtime »). Retomber en silence sur un autre dossier ferait travailler
    l'application sur des binaires qu'on venait justement d'écarter.
    """
    force = os.getenv("AGENCE_RUNTIME_DIR", "").strip()
    if force:
        chemin_force = Path(force)
        return chemin_force if chemin_force.is_dir() else None

    candidats: List[Path] = []

    # Exe PyInstaller : les datas sont extraites dans sys._MEIPASS
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidats.append(Path(meipass) / _NOM_DOSSIER)
        # Mode --onedir : le dossier peut aussi vivre À CÔTÉ de l'exe, ce qui
        # permet de mettre à jour un modèle sans recompiler.
        candidats.append(Path(sys.executable).parent / _NOM_DOSSIER)

    # Dépôt cloné : python/utils/runtime_embarque.py → racine du projet
    candidats.append(Path(__file__).resolve().parent.parent.parent / _NOM_DOSSIER)

    for c in candidats:
        try:
            if c.is_dir():
                return c
        except Exception:
            continue
    return None


def chemin(*parties: str) -> Optional[Path]:
    """Chemin embarqué s'il EXISTE réellement, sinon None."""
    base = racine()
    if base is None:
        return None
    p = base.joinpath(*parties)
    try:
        return p if p.exists() else None
    except Exception:
        return None


def premier(motif: str, *parties: str) -> Optional[Path]:
    """Premier fichier correspondant à `motif` dans un sous-dossier embarqué.

    Sert aux ressources dont le nom exact dépend de la version téléchargée au
    build (« Hermes-3-Llama-3.2-3B.Q4_K_M.gguf », « llama-server.exe »…).
    """
    dossier = chemin(*parties) if parties else racine()
    if dossier is None or not dossier.is_dir():
        return None
    try:
        for p in sorted(dossier.glob(motif)):
            if p.is_file():
                return p
    except Exception:
        pass
    return None


def manifeste() -> Dict[str, Any]:
    """Contenu de runtime/manifest.json — {} si absent ou illisible.

    Écrit par tools/preparer_runtimes.py : versions, tailles et URL réellement
    utilisées. Sert au diagnostic (« quel modèle est dans cet exe ? ») et à
    l'affichage dans l'interface.
    """
    p = chemin("manifest.json")
    if p is None:
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[runtime] manifest.json illisible : {e}")
        return {}


def resume() -> Dict[str, Any]:
    """État des runtimes embarqués, pour l'API et l'écran de configuration."""
    base = racine()
    man = manifeste()
    return {
        "dossier": str(base) if base else "",
        "actif": base is not None,
        "ollama": bool(chemin("ollama", "ollama.exe") or chemin("ollama", "bin", "ollama")),
        "ollama_modeles": bool(chemin("ollama", "models")),
        "hermes": bool(premier("llama-server*", "hermes", "bin")),
        "hermes_modele": bool(premier("*.gguf", "hermes", "models")),
        # Installeur MetaTrader embarqué (repli)…
        "mt5": bool(premier("*setup*.exe", "mt5") or premier("*.exe", "mt5")),
        # …et, mieux, le terminal PORTABLE déjà prêt : plus rien à installer.
        "mt5_portable": bool(chemin("mt5", "terminal", "terminal64.exe")),
        "manifeste": man,
    }
