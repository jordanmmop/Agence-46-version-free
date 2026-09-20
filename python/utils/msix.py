"""Application empaquetée en MSIX : détection et conséquences.

Installée depuis un MSIX (sideload ou Microsoft Store), l'application tourne
dans un conteneur dont UNE propriété change son comportement :

    LE DOSSIER D'INSTALLATION EST EN LECTURE SEULE, DÉFINITIVEMENT.

Pas « protégé par les droits » comme « Program Files », où une élévation UAC
suffirait : réellement monté en lecture seule par Windows, pour tout le monde,
y compris un processus administrateur. C'est ce qui permet à Windows de
garantir qu'une désinstallation ne laisse rien derrière elle.

Ce que cela change, concrètement
--------------------------------
Presque rien, et c'est volontaire : l'application écrit déjà TOUTES ses
données dans `~/.agence_financiere` (configuration, base SQLite, journaux,
modèles d'IA, identifiants du courtier). Ce choix, fait pour que l'installeur
Inno Setup puisse remplacer le dossier d'installation sans rien détruire,
rend l'application compatible MSIX sans rien y changer.

Un seul composant pose réellement problème : le terminal MetaTrader 5 dit
« portable ». Lancé avec `/portable`, il écrit ses profils, ses journaux et
son `config\\common.ini` JUSTE À CÔTÉ de son exécutable. Embarqué dans le
paquet, il démarrerait puis refuserait toute connexion, sans message
exploitable — un échec d'autant plus coûteux qu'il ne survient qu'à la
première tentative de trading réel, chez l'utilisateur.

`terminal_portable_utilisable()` répond donc NON dans ce cas, ce qui fait
retomber l'application sur la détection d'un MetaTrader installé
normalement — le comportement qu'elle avait déjà avant l'embarquement.

Note sur `~` : Windows redirige `%LOCALAPPDATA%` d'une application empaquetée
vers `...\\Packages\\<famille>\\LocalCache`. `~/.agence_financiere` n'est PAS
sous AppData : il n'est donc pas redirigé, et les données d'une installation
MSIX sont les mêmes que celles d'une installation Inno Setup. Passer de l'une
à l'autre conserve l'historique de trading.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Valeur rendue par l'API Windows quand le processus n'appartient à aucun
# paquet. C'est le cas NORMAL d'une exécution depuis les sources ou depuis
# une installation Inno Setup.
_APPMODEL_ERROR_NO_PACKAGE = 15700

_cache: Optional[Dict[str, Any]] = None


def _interroger_windows() -> Optional[str]:
    """Nom complet du paquet, ou None si le processus n'est pas empaqueté.

    `GetCurrentPackageFullName` est la source FAISANT AUTORITÉ : contrairement
    à un test sur le chemin de l'exécutable, elle ne se trompe ni sur une
    installation dans un dossier inhabituel, ni sur un dossier nommé
    « WindowsApps » qui n'en serait pas un.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        taille = wintypes.UINT(0)
        # Premier appel : on demande la taille nécessaire (tampon nul).
        resultat = kernel32.GetCurrentPackageFullName(ctypes.byref(taille), None)
        if resultat == _APPMODEL_ERROR_NO_PACKAGE:
            return None
        tampon = ctypes.create_unicode_buffer(taille.value or 512)
        resultat = kernel32.GetCurrentPackageFullName(ctypes.byref(taille), tampon)
        if resultat != 0:
            return None
        return tampon.value or None
    except Exception as e:
        # API absente (Windows 7/8), ctypes indisponible : l'absence de
        # réponse ne doit jamais empêcher l'application de démarrer.
        logger.debug(f"[msix] GetCurrentPackageFullName indisponible : {e}")
        return None


def _detecter() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache

    # Court-circuit explicite : permet de rejouer le comportement empaqueté
    # (ou non) en test et en développement, sans construire un vrai MSIX.
    force = (os.getenv("AGENCE_MSIX", "") or "").strip().lower()
    if force in ("1", "true", "oui", "yes"):
        _cache = {"empaquete": True, "paquet": "AGENCE_MSIX", "origine": "variable"}
        return _cache
    if force in ("0", "false", "non", "no"):
        _cache = {"empaquete": False, "paquet": "", "origine": "variable"}
        return _cache

    nom = _interroger_windows()
    if nom:
        _cache = {"empaquete": True, "paquet": nom, "origine": "api"}
        return _cache

    _cache = {"empaquete": False, "paquet": "", "origine": "api"}
    return _cache


def reinitialiser() -> None:
    """Oublie la détection mise en cache (utilisé par les tests)."""
    global _cache
    _cache = None


def empaquete() -> bool:
    """L'application tourne-t-elle depuis un paquet MSIX ?"""
    return bool(_detecter()["empaquete"])


def nom_paquet() -> str:
    """Nom complet du paquet (« Editeur.App_4.0.0.0_x64__abc123 »), ou ""."""
    return _detecter()["paquet"]


def dossier_installation() -> Optional[Path]:
    """Dossier d'installation — en LECTURE SEULE quand on est empaqueté."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        return Path(sys.executable).parent
    except Exception:
        return None


def dans_le_paquet(chemin) -> bool:
    """`chemin` se trouve-t-il DANS le dossier d'installation (donc en lecture
    seule) ?

    Répond False quand l'application n'est pas empaquetée : hors MSIX, le
    dossier d'installation reste inscriptible et rien n'a à être empêché.
    """
    if chemin is None or not empaquete():
        return False
    racine = dossier_installation()
    if racine is None:
        return False
    try:
        # `resolve()` des deux côtés : un chemin passé en relatif ou via un
        # lien symbolique doit être reconnu comme interne malgré tout.
        cible = Path(chemin).resolve()
        base = racine.resolve()
        return cible == base or base in cible.parents
    except Exception:
        return False


def terminal_portable_utilisable(chemin) -> bool:
    """Ce terminal MetaTrader portable peut-il RÉELLEMENT fonctionner ?

    Non s'il vit dans le paquet : `/portable` écrit à côté de l'exécutable, et
    le paquet est en lecture seule. Le terminal démarrerait, puis refuserait
    la connexion — panne silencieuse, découverte au pire moment.
    """
    if not chemin:
        return False
    if dans_le_paquet(chemin):
        logger.warning(
            "[msix] Terminal MetaTrader portable ignoré : il est dans le paquet "
            "MSIX, en lecture seule, et le mode /portable exige d'écrire à côté "
            "de son exécutable. L'application utilisera un MetaTrader installé "
            "normalement.")
        return False
    return True


def resume() -> Dict[str, Any]:
    """État du conteneur, pour /api/diagnostic."""
    racine = dossier_installation()
    return {
        "empaquete": empaquete(),
        "paquet": nom_paquet(),
        "dossier_installation": str(racine) if racine else "",
        "installation_en_lecture_seule": empaquete(),
        # Les données restent au même endroit, empaqueté ou non : c'est ce qui
        # permet de passer de l'installeur Inno Setup au MSIX sans rien perdre.
        "dossier_donnees": str(Path(os.path.expanduser("~")) / ".agence_financiere"),
    }
