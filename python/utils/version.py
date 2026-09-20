"""Version de l'application — SOURCE UNIQUE : le fichier `VERSION` à la racine.

Trois consommateurs lisent la même valeur, et c'est le but : le backend
(`/api/health`, `/api/diagnostic`), la compilation PyInstaller (ressource
« version » de l'exe) et le script d'installation Inno Setup (numéro affiché
par l'assistant et par « Applications installées » de Windows). Un numéro
recopié dans trois fichiers finit toujours par diverger, et c'est précisément
celui qu'affiche Windows après une mise à jour qui devient faux.

Ordre de résolution :

1. `AGENCE_VERSION` — court-circuit pour un build de test ou une reconstruction
   depuis une archive sans le fichier `VERSION` ;
2. bundle PyInstaller (`sys._MEIPASS/VERSION`) — application installée ;
3. dossier de l'exe — permet de corriger le numéro sans recompiler ;
4. racine du dépôt — exécution depuis les sources.

Aucune valeur n'est recopiée ici en repli : `0.0.0-dev` signale honnêtement
« version inconnue » plutôt que d'affirmer un numéro qui serait faux dès la
version suivante.
"""
import os
import sys
from pathlib import Path

INCONNUE = "0.0.0-dev"
_NOM_FICHIER = "VERSION"
_cache = ""


def _candidats() -> list:
    chemins = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        chemins.append(Path(meipass) / _NOM_FICHIER)
    if getattr(sys, "frozen", False):
        chemins.append(Path(sys.executable).parent / _NOM_FICHIER)
    # python/utils/version.py → racine du dépôt
    chemins.append(Path(__file__).resolve().parent.parent.parent / _NOM_FICHIER)
    return chemins


def version() -> str:
    """Version courante, p. ex. « 4.0.0 ». Ne lève jamais."""
    global _cache
    if _cache:
        return _cache
    force = (os.getenv("AGENCE_VERSION", "") or "").strip()
    if force:
        _cache = force
        return _cache
    for chemin in _candidats():
        try:
            if chemin.is_file():
                lu = chemin.read_text(encoding="utf-8").strip()
                if lu:
                    _cache = lu
                    return _cache
        except Exception:
            continue
    _cache = INCONNUE
    return _cache


def tuple_version(v=None) -> tuple:
    """« 4.1.2 » → (4, 1, 2). Sert à COMPARER deux versions.

    Sans argument : la version courante. Avec une chaîne vide ou illisible :
    (0, 0, 0) — surtout PAS la version courante, qui ferait passer un
    manifeste de mise à jour sans numéro pour « la même version que la
    mienne » au lieu de « numéro inexploitable ».

    Une comparaison de chaînes dirait que « 4.10.0 » précède « 4.9.0 » : c'est
    exactement l'erreur qui ferait manquer une mise à jour à partir de la
    dixième. Les éléments non numériques (« 4.0.0-rc1 ») sont tronqués à leur
    partie numérique, et une version illisible vaut (0, 0, 0).
    """
    texte = version() if v is None else str(v)
    morceaux = []
    for partie in texte.split(".")[:4]:
        chiffres = ""
        for c in partie.strip():
            if not c.isdigit():
                break
            chiffres += c
        morceaux.append(int(chiffres) if chiffres else 0)
    while len(morceaux) < 3:
        morceaux.append(0)
    return tuple(morceaux)


def version_windows() -> str:
    """Version au format « a.b.c.d » exigé par les ressources d'un exe Windows."""
    t = tuple_version()
    t = t + (0,) * (4 - len(t))
    return ".".join(str(n) for n in t[:4])
