"""Mises à jour : socle pour une mise à jour automatique future.

Rien n'est téléchargé, rien n'est installé, aucun serveur n'est requis : ce
module n'a qu'un rôle, savoir DIRE si une version plus récente existe. C'est
la seule brique qui manque réellement aujourd'hui — le reste (installation
par-dessus l'ancienne, conservation des données, désinstallation propre) est
déjà assuré par l'installeur Inno Setup, qui réutilise le même `AppId` d'une
version à l'autre.

Fonctionnement
--------------
La vérification est **désactivée par défaut** : sans `AGENCE_UPDATE_URL`,
`verifier()` répond « désactivé » sans ouvrir la moindre connexion. C'est
volontaire — l'application se revendique 100 % locale, et lui faire contacter
un serveur à chaque démarrage sans que personne ne l'ait demandé
contredirait sa promesse.

Le jour où un serveur existera, il suffira de publier un fichier JSON
statique (n'importe quel hébergement, y compris une release GitHub) et de
renseigner son URL :

    AGENCE_UPDATE_URL=https://exemple.tld/agence/derniere.json

    {
      "version": "4.1.0",
      "url": "https://exemple.tld/AgenceNumerique-Setup-4.1.0.exe",
      "notes": "Correction du calcul de drawdown",
      "obligatoire": false,
      "sha256": "…"
    }

Voir `installer/updates/manifest-exemple.json`. L'étape suivante — télécharger
l'installeur et le lancer en mode silencieux (`/SILENT`) — tient alors en
quelques lignes, l'installeur sachant déjà se mettre à jour par-dessus
lui-même.
"""
import json
import logging
import os
import sys
from typing import Any, Dict

from utils.version import tuple_version, version

logger = logging.getLogger(__name__)

VARIABLE_URL = "AGENCE_UPDATE_URL"
DELAI_S = 6.0          # une vérification ne doit jamais retarder l'application


def url_configuree() -> str:
    return (os.getenv(VARIABLE_URL, "") or "").strip()


def chemin_installation() -> str:
    """Dossier d'installation de l'application, ou "" si elle n'est pas installée.

    Un futur installeur de mise à jour a besoin de savoir OÙ écrire. Inno Setup
    l'enregistre dans la base de registre au moment de l'installation ; en
    exécution depuis les sources, l'information n'existe pas et c'est normal.
    """
    if not getattr(sys, "frozen", False):
        return ""
    if sys.platform == "win32":
        try:
            import winreg
            for ruche in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(ruche, r"Software\AgenceNumerique") as cle:
                        valeur, _ = winreg.QueryValueEx(cle, "InstallPath")
                        if valeur:
                            return str(valeur)
                except OSError:
                    continue
        except Exception:
            pass
    return os.path.dirname(sys.executable)


def _telecharger_manifeste(url: str) -> Dict[str, Any]:
    import urllib.request
    # Même convention que le reste de l'application : magasins de certificats
    # en cascade (un antivirus qui inspecte le HTTPS présente sa propre
    # autorité) et proxy neutralisé pour les appels locaux.
    requete = urllib.request.Request(
        url, headers={"User-Agent": f"AgenceNumerique/{version()}"})
    try:
        from utils import reseau
        with reseau.ouvrir(requete, timeout=DELAI_S) as reponse:
            brut = reponse.read(64 * 1024)
    except Exception:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(requete, timeout=DELAI_S) as reponse:
            brut = reponse.read(64 * 1024)
    data = json.loads(brut.decode("utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


def verifier(url: str = "") -> Dict[str, Any]:
    """Une version plus récente est-elle publiée ? Ne lève JAMAIS.

    Réponse : `disponible` (bool), `version_installee`, `version_publiee`,
    `url`, `notes`, `actif` (la vérification est-elle configurée) et, en cas
    de problème, `erreur`. Un serveur injoignable n'est pas une panne de
    l'application : elle continue exactement comme avant.
    """
    reponse: Dict[str, Any] = {
        "actif": False,
        "disponible": False,
        "version_installee": version(),
        "version_publiee": "",
        "url": "",
        "notes": "",
        "installation": chemin_installation(),
    }
    cible = (url or url_configuree()).strip()
    if not cible:
        return reponse
    reponse["actif"] = True
    try:
        manifeste = _telecharger_manifeste(cible)
    except Exception as e:
        reponse["erreur"] = f"{type(e).__name__}: {e}"
        return reponse

    publiee = str(manifeste.get("version", "") or "").strip()
    reponse["version_publiee"] = publiee
    reponse["url"] = str(manifeste.get("url", "") or "")
    reponse["notes"] = str(manifeste.get("notes", "") or "")[:1000]
    reponse["obligatoire"] = bool(manifeste.get("obligatoire", False))
    reponse["sha256"] = str(manifeste.get("sha256", "") or "")
    if not publiee:
        reponse["erreur"] = "manifeste sans numéro de version"
        return reponse
    reponse["disponible"] = tuple_version(publiee) > tuple_version(version())
    return reponse
